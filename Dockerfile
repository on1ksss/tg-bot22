FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
RUN useradd -m bot && mkdir /data && chown bot /data
USER bot
# контейнер «здоров», если бот недавно обновлял heartbeat-файл
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
  CMD python -c "import os,time,sys; sys.exit(0 if time.time()-os.path.getmtime('/tmp/heartbeat')<120 else 1)"
CMD ["python", "-m", "app.bot"]
