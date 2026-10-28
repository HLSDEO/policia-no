FROM python:3.12-slim
RUN pip install --no-cache-dir "locust==2.32.4"
WORKDIR /carga
COPY locustfile.py gerar_ufdr.py ./
CMD ["locust", "-f", "locustfile.py", "--host", "http://web:80"]
