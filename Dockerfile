# Dockerfile for sandbox in which python 3 code is run
FROM python:3-buster

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Europe/London \
	PATH="/home/matchmaker/.local/bin:${PATH}" \
    PYTHONPATH="/home/matchmaker:/home/matchmaker/app:${PYTHONPATH}"

# Set up user
RUN useradd --create-home --shell /bin/bash matchmaker
WORKDIR /home/matchmaker

# Install python libraries as user
USER matchmaker
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt
USER root

# Copy scripts
COPY app /home/matchmaker/app
ADD --chown=matchmaker https://raw.githubusercontent.com/AI-Wars-Soc/common/main/default_config.yml /home/matchmaker/default_config.yml
RUN chown -R matchmaker /home/matchmaker/app

WORKDIR /home/matchmaker
USER matchmaker
CMD [ "python",  "app/main.py" ]
