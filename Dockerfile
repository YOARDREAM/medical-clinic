FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY web ./web
ENV APP_HOST=0.0.0.0
EXPOSE 8000
USER 65534
CMD ["python", "app.py"]
