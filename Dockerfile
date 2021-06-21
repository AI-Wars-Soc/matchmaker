# Dockerfile for sandbox in which python 3 code is run
FROM python:3-buster

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
COPY app/default_config.yml /home/matchmaker/default_config.yml
RUN chown -R matchmaker /home/matchmaker/app
ENV PYTHONPATH="/home/matchmaker:/home/matchmaker/app:${PYTHONPATH}"

WORKDIR /home/matchmaker
USER matchmaker
CMD [ "python",  "app/main.py" ]
