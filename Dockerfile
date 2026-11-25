FROM python:3.11-slim
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user SAHHAB_HOST=0.0.0.0 PORT=7860 SAHHAB_DIR=/home/user/Sahhab PYTHONUNBUFFERED=1
WORKDIR /home/user/app
COPY --chown=user app.py .
EXPOSE 7860
CMD ["python", "app.py"]
