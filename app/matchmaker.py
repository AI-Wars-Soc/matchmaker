import json
import logging
import math
import random
import time
import traceback
from datetime import datetime, timezone
from threading import Thread
from typing import List, Tuple

import cuwais
import numpy
import requests
from cuwais.common import Outcome
from cuwais.config import config_file
from cuwais.database import Submission, Result, Match
from cuwais.gamemodes import Gamemode
from sqlalchemy import func, and_

logging.basicConfig(
    format='%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s' if config_file.get("debug") else '%(message)s',
    level=logging.DEBUG if config_file.get("debug") else logging.WARNING,
    datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)


def _get_all_latest_healthy_submissions() -> List[Tuple[Submission, float]]:
    """Gets a list of submissions along with their healths from 1 to 0.
    Submissions with health 0 are omitted as they should not be run"""
    with cuwais.database.create_session() as database_session:
        healthy_subq = database_session.query(
            Submission.id,
            func.count(Result.id).label('healthies')
        ).join(Submission.results)\
            .group_by(Submission.id)\
            .filter(Result.healthy == True)\
            .subquery()

        most_recent_subq = database_session.query(
            Submission.user_id,
            func.max(Submission.submission_date).label('maxdate')
        ).join(
            healthy_subq,
            and_(
                Submission.id == healthy_subq.c.id
            )
        ).group_by(Submission.user_id)\
            .filter(Submission.active == True)\
            .subquery()

        total_results_subq = database_session.query(
            Submission.id,
            func.count(Result.id).label('total')
        ).join(Submission.results)\
            .group_by(Submission.id)\
            .subquery()

        res = database_session.query(
            Submission,
            healthy_subq.c.healthies,
            total_results_subq.c.total
        ).join(
            healthy_subq,
            and_(
                Submission.id == healthy_subq.c.id
            )
        ).join(
            total_results_subq,
            and_(
                Submission.id == total_results_subq.c.id
            )
        ).join(
            most_recent_subq,
            and_(
                Submission.user_id == most_recent_subq.c.user_id,
                Submission.submission_date == most_recent_subq.c.maxdate
            )
        ).all()

    return [(sub, h/t) for sub, h, t in res]


def _get_all_untested_submissions() -> List[Submission]:
    logger.debug(f"Getting untested submissions")
    with cuwais.database.create_session() as database_session:
        logger.debug(f"Got session")
        # All submissions with no results
        res = database_session.query(Submission)\
            .outerjoin(Submission.results)\
            .filter(Result.id == None)\
            .all()

    logger.debug(f"Untested submissions: {res}")
    return res


def _calculate_delta_scores_pair(a_score, b_score, winner_weight):
    """
    Typical Elo rating delta calculation for a 2-player game

    Winner weight is the expected amount that player 1 will win from the given game,
    I.e. 1 for a win-loss situation, 0.5 for a draw/stalemate
    """
    k = float(config_file.get("score_turbulence"))

    a_rating = 10 ** (a_score / 400)
    b_rating = 10 ** (b_score / 400)

    a_expected = a_rating / (a_rating + b_rating)

    delta = k * (winner_weight - a_expected)

    return delta


def _calculate_delta_scores(outcomes: List[Outcome], current_elos: List[float]):
    """
    Calculates a set of Elo deltas for a collection of results for submissions with current estimated Elos

    Uses an arbitrary algorithm explained below.

    This algorithm must satisfy a few constraints:
    For 2-player games it must behave as the typical Elo algorithm above
    For every game, the total amount of Elo won and lost must be 0 (No net gain/loss in the system)
    If a player wins they should gain Elo and if they lose they should lose Elo
    After a settling period, the long-term behaviour of any player's Elo should be stationary, assuming a fixed
    probability of winning
    Players with a handicap should be penalised less for a loss. A handicap may be either a high score difference,
    or imbalanced teams.

    Idea: Group players into faux teams based on the outcome - win, loss or draw.
    Sum the Elo ratings for each team and calculate the 3 pair swings as if each team were a player.
    All 3 pairings should sum to 0, so spread out each Elo delta across each team member to ensure no net change

    E.g. 5 players, (Win, Loss, Draw, Loss, Draw), with Elo ratings (1000, 2000, 3000, 4000, 5000).
    For the teams (Win, Loss, Draw) we have effective Elos of (1000, 6000, 8000)
    So we calculate the swings for each pairing (WinLoss, WinDraw, LossDraw)
    And distribute Elo,
    (Delta Win, Delta Loss, Delta Draw) = ((WinLoss + WinDraw) / 1, (-WinLoss - LossDraw) / 2, (LossDraw - WinDraw) / 2)

    There are a few edge cases here (that all come up pretty often):
     - There may be 0 players on a 'team'
     - All players may be on one team

    To solve the empty teams, we simply omit the pairing if one of the teams has 0 players.
    This still preserves the closed team assumption

    To solve the single team issue, we add an extra step:
    If there is only one team, we assume that all of the players within each that have drawn.
    To calculate the score in this case we assume that every player draws with their Elo opposite.

    E.g. If 5 players win with ratings (1000, 2000, 3000, 4000, 5000), we say that player 1 drew with player 5,
    2 with 4 and player 3's rating remains unchanged.
    """

    # Group
    counts = {o: 0 for o in Outcome}
    totals = {o: 0 for o in Outcome}
    group_elos = {o: [] for o in Outcome}
    for outcome, elo in zip(outcomes, current_elos):
        counts[outcome] += 1
        totals[outcome] += elo
        group_elos[outcome].append(elo)

    # Inter-Group deltas
    win_loss = 0
    win_draw = 0
    loss_draw = 0
    if counts[Outcome.Win] > 0 and counts[Outcome.Loss] > 0:
        win_loss = _calculate_delta_scores_pair(totals[Outcome.Win], totals[Outcome.Loss], 1)
    if counts[Outcome.Win] > 0 and counts[Outcome.Draw] > 0:
        win_draw = _calculate_delta_scores_pair(totals[Outcome.Win], totals[Outcome.Draw], 1)
    if counts[Outcome.Loss] > 0 and counts[Outcome.Draw] > 0:
        loss_draw = _calculate_delta_scores_pair(totals[Outcome.Draw], totals[Outcome.Loss], 1)

    # Intra-Group deltas
    single_team_elos = []
    single_team_outcome = None
    if counts[Outcome.Win] == counts[Outcome.Loss] == 0:
        single_team_outcome = Outcome.Draw
    elif counts[Outcome.Win] == counts[Outcome.Draw] == 0:
        single_team_outcome = Outcome.Loss
    elif counts[Outcome.Loss] == counts[Outcome.Draw] == 0:
        single_team_outcome = Outcome.Win

    if single_team_outcome is not None:
        single_team_elos = sorted(group_elos[single_team_outcome])

    # Player deltas
    deltas = []
    for outcome, elo in zip(outcomes, current_elos):
        # Inter-Group portion
        if outcome == Outcome.Win:
            delta = win_loss + win_draw
            delta /= counts[Outcome.Win]
        elif outcome == Outcome.Loss:
            delta = -win_loss - loss_draw
            delta /= counts[Outcome.Loss]
        else:  # result.outcome == Outcome.Draw
            delta = loss_draw - win_draw
            delta /= counts[Outcome.Draw]

        # Intra-Group portion for single team result sets
        if outcome == single_team_outcome:
            position_in_team = single_team_elos.index(elo)
            opponent_position = len(single_team_elos) - position_in_team - 1
            opponent_elo = single_team_elos[opponent_position]

            if opponent_position != position_in_team:
                delta += _calculate_delta_scores_pair(elo, opponent_elo, 0.5)

        deltas.append(delta)

    logger.debug(f"Calculated some ELO Deltas: {deltas}, winners: {outcomes}")

    # Assertions
    assert len(deltas) == len(outcomes)
    assert -0.000001 < sum(deltas) < 0.000001
    # Check that this works the same as Elo for two player games
    if len(outcomes) == 2:
        if outcomes[0] == outcomes[1] == Outcome.Draw:
            exp_delta = _calculate_delta_scores_pair(min(current_elos), max(current_elos), 0.5)
            assert math.isclose(max(deltas), exp_delta, rel_tol=1e-9, abs_tol=0.0)
        if outcomes[0] != outcomes[1]:
            v = 1 if outcomes[0] == Outcome.Win else 0
            exp_delta = _calculate_delta_scores_pair(current_elos[0], current_elos[1], v)
            assert math.isclose(deltas[0], exp_delta, rel_tol=1e-9, abs_tol=0.0)

    return deltas


def _get_elos(submission_ids: list):
    init = float(config_file.get("initial_score"))

    with cuwais.database.create_session() as database_session:
        subq = database_session.query(
            Submission.user_id,
            func.sum(Result.points_delta).label('elo')
        ).filter(Submission.id.in_(submission_ids))\
            .join(Submission.results)\
            .group_by(Submission.user_id)\
            .subquery("t1")

        res = database_session.query(
            Submission.id,
            subq.c.elo
        ).filter(
            Submission.id.in_(submission_ids),
            Submission.user_id == subq.c.user_id
        ).all()

    elo_lookup = {v["id"]: v["elo"] for v in res}

    elos = [elo_lookup.get(i, 0) + init for i in submission_ids]

    logger.debug(f"Calculated some ELOs: {elos}")

    return elos


def _save_result(submissions: List[Submission],
                 result: dict,
                 update_scores: bool) -> Match:
    logger.debug(f"Saving result: submissions: {submissions}, result: {result}")
    submission_ids = [submission.id for submission in submissions]
    submission_results = result["submission_results"]
    if not len(submission_ids) == len(submission_results):
        raise ValueError("Bad submission ID count")

    deltas = [0] * len(submission_ids)
    if update_scores:
        elos = _get_elos(submission_ids)
        outcomes = [Outcome(r["outcome"]) for r in submission_results]
        deltas = _calculate_delta_scores(outcomes, elos)

    with cuwais.database.create_session() as database_session:
        match = Match(match_date=datetime.now(tz=timezone.utc), recording=json.dumps(result["recording"]))

        database_session.add(match)

        for submission_id, submission_result, delta in zip(submission_ids, submission_results, deltas):
            result = Result(match=match,
                            submission_id=submission_id,
                            outcome=int(submission_result["outcome"]),
                            healthy=submission_result["healthy"],
                            points_delta=delta,
                            player_id=submission_result["player_id"],
                            prints=submission_result["printed"],
                            result_code=submission_result["result_code"])
            database_session.add(result)

        database_session.commit()

    return match


def _run_match(gamemode: Gamemode, options, submissions: List[Submission]) -> dict:
    submission_hashes = [submission.files_hash for submission in submissions]
    return requests.get('http://runner:8080/run', {
        **options,
        "gamemode": gamemode.name,
        "submissions": ",".join(submission_hashes),
    }).json()


def _run_typical_match(gamemode: Gamemode, options) -> bool:
    logger.debug("Starting typical match")

    # Get n sumbissions
    submissions = []
    if gamemode.player_count > 0:
        newest = _get_all_latest_healthy_submissions()
        if len(newest) < gamemode.player_count:
            return False
        newest_submissions = [submission for submission, _ in newest]
        healths = numpy.array([health for _, health in newest])
        logger.debug(f"Got newest submissions {newest_submissions}, with healths {healths}")
        healths *= (1 / numpy.sum(healths))
        i_submissions = numpy.random.choice(len(newest_submissions), size=gamemode.player_count, replace=False, p=healths)
        for i in i_submissions:
            submissions.append(newest_submissions[i])

    # Run & parse
    logger.debug("Running typical match")
    result = _run_match(gamemode, options, submissions)
    logger.debug(f"Ran typical match. Result {result}")

    # Save
    update_scores = True in {r["healthy"] for r in result["submission_results"]}
    _save_result(submissions, result, update_scores)

    return True


def _run_test_match(gamemode: Gamemode, options) -> bool:
    logger.debug("Starting test match")

    # Get n sumbissions
    submissions = []
    if gamemode.player_count > 0:
        newest = _get_all_untested_submissions()
        if len(newest) == 0:
            return False
        submissions = [random.choice(newest)] * gamemode.player_count

    # Run & parse
    logger.debug("Running test match")
    result = _run_match(gamemode, options, submissions)
    logger.debug(f"Ran test match. Result {result}")

    # Record successes and failures
    _save_result(submissions, result, False)

    return True


class Matchmaker(Thread):
    def __init__(self, gamemode: Gamemode, options: dict, seconds_per_run: int):
        super().__init__()
        self.gamemode = gamemode
        self.options = options
        self.seconds_per_run = seconds_per_run
        self.daemon = True

    def _run_one(self) -> bool:
        try:
            # Prioritise running test matches
            ran = _run_test_match(self.gamemode, self.options)
            if ran:
                return True

            return _run_typical_match(self.gamemode, self.options)
        except requests.exceptions.RequestException as e:
            logger.error("Could not connect to the submission runner: " + str(e.strerror))
        except Exception:
            traceback.print_exc()
        return False

    def run(self) -> None:
        if self.gamemode.player_count < 1:
            return

        while True:
            start = time.process_time_ns()

            success = self._run_one()
            diff = (time.process_time_ns() - start) / 1e9

            wait_time = self.seconds_per_run - diff

            if not success:
                wait_time + random.randint(1, max(2, self.seconds_per_run))  # Pause for a while on failure

            wait_time += 0.05 * self.seconds_per_run * (random.random() - 0.5)  # Pause for a while so we're not in sync

            time.sleep(max(0.0, wait_time))


def run_matchmakers():
    # Get some options
    gamemode, options = Gamemode.get_from_config()
    matchmakers = int(config_file.get("matchmaker.matchmakers"))
    seconds_per_run = int(config_file.get("matchmaker.target_seconds_per_game"))

    # Start up matchmakers
    for i in range(matchmakers - 1):
        matchmaker = Matchmaker(gamemode, options, seconds_per_run)
        matchmaker.start()

    # Have the main thread run the last one
    matchmaker = Matchmaker(gamemode, options, seconds_per_run)
    matchmaker.run()
