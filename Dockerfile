FROM python:3.10
RUN apt update
RUN apt upgrade
ADD scanner.py
ADD run.sh
ADD install.sh
RUN bash install.sh
CMD ["bash", "run.sh"]