FROM python:3.10
ADD scanner.py .
ADD run.sh .
ADD install.sh .
ADD requirements.txt .
RUN bash install.sh
CMD ["bash", "run.sh"]