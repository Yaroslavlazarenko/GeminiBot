FROM registry.access.redhat.com/ubi9/python-311:latest

# Переключаемся на root для установки пакетов
USER 0
RUN dnf install -y postgresql-devel gcc python3-devel \
    && dnf clean all \
    && rm -rf /var/cache/dnf

# Создаем и переключаемся на непривилегированного пользователя
USER 1001
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]