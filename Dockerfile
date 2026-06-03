FROM python:3.11-slim
 
WORKDIR /app
 
# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
 
# Copy application
COPY main.py .
 
# Non-root user for security
RUN useradd -m appuser
USER appuser
 
EXPOSE 8080
 
CMD ["python", "main.py"]