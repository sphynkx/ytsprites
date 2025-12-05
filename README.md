[ytsprites](https://github.com/sphynkx/ytsprites) is supplemental service for [yurtube app](https://github.com/sphynkx/yurtube), based on gRPC+protobuf. It generates thumbnail preview sprites for uploading videos.

## Install and configure.
Instructions for Fedora..

### ffmpeg
Install ffmpeg:
```bash
sudo dnf install -y ffmpeg
```
If not found, enable RPM Fusion (free + nonfree), then install:
```bash
sudo dnf install -y https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm  -E %fedora).noarch.rpm https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm
```
Optionaly - swap ffmpeg-free to full ffmpeg if your system has ffmpeg-free preinstalled:
```bash
sudo dnf -y swap ffmpeg-free ffmpeg --allowerasing
```
Install ffmpeg (ffprobe comes with the same package)
```bash
sudo dnf install -y ffmpeg
```
Verify:
```bash
which ffmpeg && ffmpeg -version
which ffprobe && ffprobe -version
```


### app
Download service from repository and install:
```bash
cd /opt
git clone https://github.com/sphynkx/ytsprites
cd ytsprites
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r install/requirements.txt
deactivate
```

Make sure that proto-file `proto/ytsprites.proto` is same as in yutrube installation. If changes are made - it need to regenerate by commands:
```bash
cd proto
./gen_proto.sh
cd ..
```

Configure and run as systemd service.
```bash
cp install/ytsprites.service /etc/systemd/system/
systemctl enable --now ytsprites
journalctl -u ytsprites -f
```


## Run via docker
As above:
```bash
git clone https://github.com/sphynkx/ytsprites
```
and: 
```bash
cd ytsprites/install/docker
docker-compose up -d --build
docker-compose logs -f
```