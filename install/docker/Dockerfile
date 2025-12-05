FROM fedora:40

RUN dnf -y update && dnf -y install dnf-plugins-core git wget

# Install RPM Fusion for full FFmpeg support (non-free codecs)
RUN dnf install -y https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm \
    https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm

RUN dnf install -y \
    python3.12 \
    python3.12-devel \
    python3-pip \
    gcc \
    ffmpeg \
    ffmpeg-devel \
    && dnf clean all

WORKDIR /app

COPY install/requirements.txt .
RUN pip3.12 install --upgrade pip && \
    pip3.12 install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x run.sh install/gen_proto.sh

# Generate Protobuf stubs inside the container
RUN ./install/gen_proto.sh

# Expose the gRPC port (matches config default)
EXPOSE 60051

CMD ["python3.12", "main.py"]