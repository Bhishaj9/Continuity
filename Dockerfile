# Use official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# 1. INSTALL SYSTEM DEPENDENCIES
# Fix: 'libgl1-mesa-glx' is deprecated. Using 'libgl1' and 'libglx-mesa0' instead.
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libgl1 \
    libglx-mesa0 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 2. Install Python Dependencies (Cached)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Copy Application Code
COPY . .

# 4. Create writable directory for outputs
RUN mkdir -p outputs && chmod 777 outputs

# 5. Expose Port
EXPOSE 7860

# 6. Start Server
CMD ["python", "server.py"]
