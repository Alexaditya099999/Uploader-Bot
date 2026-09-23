# Set the base image
FROM python:3.10.6-slim-buster

# Install required system packages
RUN apt-get update && \
    apt-get install -y ffmpeg libsm6 libxext6 curl && \
    apt-get install -y build-essential python3-dev && \
    apt-get clean

# Install yt-dlp
RUN curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp && \
    chmod a+rx /usr/local/bin/yt-dlp

# Set the working directory
WORKDIR /app

# Copy the requirements file to the working directory
COPY requirements.txt .

# Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Set the command to run the Python script
CMD ["python", "main.py"]