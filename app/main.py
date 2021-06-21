from gevent import monkey
monkey.patch_all()

import cuwais.database

from app import matchmaker

if __name__ == '__main__':
    cuwais.database.create_tables()
    matchmaker.run_matchmakers()
