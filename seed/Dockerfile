FROM python:3.12-slim
WORKDIR /seed
ENV PYTHONUNBUFFERED=1
COPY send_emails.py .
COPY emails ./emails
CMD ["python", "send_emails.py"]
