# SmartBin

Event-driven smart bin monitoring for a public market. Each bin measures how full it is only when something happens, and a cloud service turns those readings into alerts, a forecast and a collection route.

* **IoT:** ESP32 with an ultrasonic sensor and a lid switch
* **Cloud:** a Docker container running on AWS EC2 (Kubernetes demo included)

```
ESP32 bin  --- HTTP --->  AWS EC2 (Docker container)  <--- browser
(sensor + lid switch)     API + database + dashboard      (dashboard)
```

## Contents

1. [Why event-driven](#1-why-event-driven)
2. [Project structure](#2-project-structure)
3. [Run it on your computer](#3-run-it-on-your-computer)
4. [Send a test reading](#4-send-a-test-reading)
5. [Run the tests](#5-run-the-tests)
6. [Deploy on AWS EC2](#6-deploy-on-aws-ec2)
7. [Run on Kubernetes (Docker Desktop)](#7-run-on-kubernetes-docker-desktop)
8. [Device firmware (Wokwi and real ESP32)](#8-device-firmware-wokwi-and-real-esp32)
9. [How it works](#9-how-it-works)
10. [API](#10-api)
11. [Settings](#11-settings)
12. [Limits](#12-limits)

## 1. Why event-driven

Fill level only changes when the lid opens, so measuring every minute wastes battery, data and storage.

| Trigger | When | Why |
|---|---|---|
| `lid_close` | lid closed again, after a short settle time | the only moment fill can change |
| `refresh` | someone presses Refresh on the dashboard | fresh reading on demand |
| `heartbeat` | nothing sent for an hour | proves the device is alive |

The **Messages saved** card on the dashboard compares real readings with a measure-every-minute design.

## 2. Project structure

```
smartbin/
├── frontend/       web dashboard (index.html, style.css, app.js)
├── backend/        cloud service (API, database, tests)
│   ├── app/
│   └── tests/
├── firmware/       IoT device code
│   ├── esp32_smartbin/   real ESP32 board
│   └── wokwi/            online simulator
├── Dockerfile      builds the image (backend + frontend)
├── .env.example    settings template
└── README.md       this file (every step and command)
```

## 3. Run it on your computer

Works the same on Windows, Ubuntu and macOS. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine on Ubuntu) and run these from the project root.

```
cp .env.example .env
docker build -t smartbin:v1 .
docker run -d --name smartbin -p 8000:8000 --env-file .env -v smartbin-data:/data smartbin:v1
```

(Windows Command Prompt: use `copy` instead of `cp`. PowerShell accepts `cp`.)

Open http://localhost:8000. If you turned on `PROTECT_DASHBOARD` or set an `ADMIN_TOKEN`, paste the admin token in the box at the top.

Stop and remove it:

```
docker rm -f smartbin
```

Without Docker (Python 3.10+). On Windows use `python`, on Ubuntu use `python3`:

```
cd backend
python3 -m pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The dashboard starts empty. A bin appears when a device sends its first reading, either the Wokwi demo or a real ESP32 (see [section 8](#8-device-firmware-wokwi-and-real-esp32)).

## 4. Send a test reading

Open http://localhost:8000/docs, expand `POST /api/readings`, click **Try it out**, set `X-API-Key` to the `API_KEY` from your `.env`, and use this body:

```json
{ "bin_id": "Bin-01", "distance_cm": 45, "trigger": "lid_close" }
```

On Linux or macOS you can use curl instead:

```
curl -X POST http://localhost:8000/api/readings -H "X-API-Key: change-me-device-key" -H "Content-Type: application/json" -d '{"bin_id":"Bin-01","distance_cm":45,"trigger":"lid_close"}'
```

## 5. Run the tests

```
cd backend
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
```

## 6. Deploy on AWS EC2

Same flow as the lecture practical: launch an EC2 server, open the ports, install software, run the app, optionally add a domain with Route 53. The difference from the lecture is that we install Docker and run the app in a container instead of installing Apache.

### Part 1: Launch the EC2 server

1. AWS Console, open **EC2**, then **Launch instance**.
2. Name: `smartbin-server`
3. AMI: **Amazon Linux 2023**
4. Instance type: `t2.micro` (free tier). Use `t3.micro` if `t2.micro` is not offered in your region.
5. Key pair: create one (RSA) and choose the **.ppk** format (PuTTY). Keep the file private and never put it in the project folder. You do not need it if you connect with the browser button in Part 2.
6. Security group, allow:
   * **SSH (22)** from **Anywhere (0.0.0.0/0)**. The browser Connect button (EC2 Instance Connect) reaches your server from AWS's own addresses, not yours, so "My IP" makes it time out. This is fine for a short lab. If you only use PuTTY, "My IP" is safer.
   * **HTTP (80)** from **Anywhere (0.0.0.0/0)**. The Wokwi simulator runs on the internet, so it needs to reach port 80.
7. Launch the instance and copy its **Public IPv4 address**.

### Part 2: Connect and install Docker

Select the instance, click **Connect** in the EC2 console and use **EC2 Instance Connect**. It opens a terminal in your browser, so it works the same on Windows, Linux and macOS. (PuTTY with your `.ppk` file also works.)

```
sudo yum install docker git -y
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user
exit
```

Connect again so the group change applies.

### Part 3: Get the code and run it

```
git clone https://github.com/YOUR_USERNAME/smartbin.git
cd smartbin
cp .env.example .env
nano .env
```

In `.env` set two different random values for `API_KEY` and `ADMIN_TOKEN`, and set `PROTECT_DASHBOARD=true`. Save with Ctrl+O, Enter, Ctrl+X.

Build and run:

```
docker build -t smartbin:v1 .
docker run -d --name smartbin -p 80:8000 --env-file .env -v smartbin-data:/data --restart unless-stopped smartbin:v1
docker ps
```

Check on the server:

```
curl http://localhost/health
```

You should see `{"status":"ok"}`.

### Part 4: Open the dashboard

Open `http://<EC2-PUBLIC-IP>/` in your browser (http, not https) and paste the admin token in the box at the top.

The dashboard starts empty. A bin appears when a device sends its first reading. Use the Wokwi demo (section 8) and set `SERVER` to `http://<EC2-PUBLIC-IP>` and `API_KEY` to the value in your `.env`.

### Part 5 (optional): Custom domain with Route 53

1. Open **Route 53**, register a domain or use an existing one, for example `studentdemo.com`.
2. Create a hosted zone.
3. Create an **A record**: name `www`, value = the EC2 public IP.
4. Visit `http://www.studentdemo.com`. The dashboard loads through the domain.

The public IP changes every time the instance is stopped and started. Attach an **Elastic IP** to the instance if you want it to stay the same. Update `SERVER` in the firmware if the address changes.

### Update after a code change

```
cd smartbin
git pull
docker rm -f smartbin
docker build -t smartbin:v1 .
docker run -d --name smartbin -p 80:8000 --env-file .env -v smartbin-data:/data --restart unless-stopped smartbin:v1
```

Stored readings stay in the `smartbin-data` volume. To start with clean data, run `docker rm -f smartbin` and `docker volume rm smartbin-data` first.

### When you are finished

Stop the instance in the EC2 console so you are not charged.

## 7. Run on Kubernetes (Docker Desktop)

Same method as the lecture: enable Kubernetes in Docker Desktop, then use `kubectl` commands. Works on Windows, Ubuntu and macOS.

### Step 1: Enable Kubernetes

Docker Desktop, Settings, Kubernetes, **Enable Kubernetes**, Apply & Restart. Wait until it starts, then check:

```
kubectl version --client
kubectl cluster-info
kubectl get nodes
```

### Step 2: Build the image

Run from the project root (the folder with the `Dockerfile`).

```
docker build -t smartbin:v1 .
docker images
```

### Step 3: Create the deployment

```
kubectl create deployment smartbin --image=smartbin:v1
kubectl get deployments
kubectl get pods
```

The pod does not read your `.env` file, so it starts with the defaults: device key `change-me-device-key` and no admin token. To set your own (this restarts the pod):

```
kubectl set env deployment/smartbin API_KEY=your-device-key ADMIN_TOKEN=your-admin-token
```

### Step 4: Expose and open it

```
kubectl expose deployment smartbin --type=NodePort --port=8000
kubectl get svc
kubectl port-forward service/smartbin 8000:8000
```

Open http://localhost:8000 in the browser. Keep this terminal open while you use it.

### Self-healing demo

Open a second terminal:

```
kubectl get pods
kubectl delete pod <pod-name>
kubectl get pods
```

Kubernetes creates a new pod by itself. Stored readings live inside the pod, so they are cleared when the pod is replaced.

Do not scale this app to more than one replica: the database is a single file inside each pod, so every copy would have different data.

### Clean up

```
kubectl delete service smartbin
kubectl delete deployment smartbin
```

## 8. Device firmware (Wokwi and real ESP32)

* `firmware/wokwi/` the logic with short demo timings for the Wokwi online simulator.
* `firmware/esp32_smartbin/` the same logic for a real ESP32 with an HC-SR04 ultrasonic sensor under the lid and a reed switch as the lid sensor. It has not been tested on hardware yet, so calibrate it yourself.

### Device behaviour

1. Lid opens, count it.
2. Lid closes, wait for the waste to settle, then measure and send a reading (`lid_close`).
3. Nothing sent for an hour, send a `heartbeat`.
4. While idle, keep one request open to the server so a dashboard Refresh arrives instantly (`refresh`).

The open request keeps Wi-Fi awake, so the real device suits a bin with USB or mains power.

### Wokwi demo (virtual ESP32)

A virtual ESP32 with an HC-SR04 and a lid switch posts real readings to your server.

1. On wokwi.com start a new ESP32 project.
2. Paste in `sketch.ino` and `diagram.json` from `firmware/wokwi/`.
3. Set `SERVER` (for example `http://<EC2-PUBLIC-IP>`) and `API_KEY` in `sketch.ino`. `API_KEY` must be the same key as in your `.env`. Never commit real values.
4. Press Start and open the serial monitor.
5. Click the HC-SR04 to set a distance (10 to 80 cm), then slide the lid switch open and closed.
6. A few seconds after the lid closes a reading is sent and `Bin-WOKWI` shows up on the dashboard.

Timings are shortened for the demo (2 s settle, 3 s merge gap, 3 s long poll, 2 min heartbeat). Above roughly 100 cm the server reports a sensor fault. The server must accept inbound HTTP on port 80 from the internet (check the AWS security group).

### Real ESP32 board

Wiring:

| Part | ESP32 pin |
|---|---|
| HC-SR04 TRIG | GPIO 5 |
| HC-SR04 ECHO | GPIO 18 (use a voltage divider, ECHO is 5 V) |
| Reed switch | GPIO 27 and GND |

Open `firmware/esp32_smartbin/esp32_smartbin.ino` in the Arduino IDE and set `WIFI_SSID`, `WIFI_PASS`, `SERVER` and `API_KEY`. `API_KEY` must match the one in the server's `.env`. Do not commit your real Wi-Fi password or key.

## 9. How it works

* **Fill %** = (depth - distance) / (depth - dead zone) x 100, clamped to 0..100. Impossible distances raise a sensor fault.
* **Alerts** at 80% (nearly full) and 95% (full). They clear below 70%, or when a collection is detected.
* **Collection detection** is a drop of 30 points or more between two readings.
* **Forecast** is a straight line fitted to recent readings since the last collection.
* **Route** orders bins full, then nearly full, then soonest to overflow.
* **Refresh** keeps one request open, so the device gets the command instantly. There is a 15 s cooldown per bin.
* **Offline detection** raises an alert after 150 minutes of silence.

## 10. API

Interactive docs live at `/docs`.

| Endpoint | Who | Purpose |
|---|---|---|
| `POST /api/readings` | device (`X-API-Key`) | send a reading |
| `GET /api/bins/{id}/commands/next?wait=20` | device | wait for a refresh request |
| `GET /api/bins`, `/api/priority`, `/api/summary`, `/api/alerts` | dashboard | state, route, savings, alerts |
| `GET /api/bins/{id}/readings` | dashboard | history |
| `POST /api/bins/{id}/refresh` | dashboard | ask for a fresh reading |
| `POST /api/bins`, `PATCH /api/bins/{id}` | operator (`X-Admin-Token`) | add or edit a bin |
| `POST /api/alerts/{id}/ack`, `/resolve` | operator | handle alerts |

## 11. Settings

Set these in `.env`.

| Variable | Meaning |
|---|---|
| `API_KEY` | key the devices send with every reading |
| `ADMIN_TOKEN` | token for operator actions |
| `PROTECT_DASHBOARD` | `true` makes the dashboard need the token |

Advanced values (thresholds, bin depth, cooldown) have defaults in `backend/app/config.py` and can also be set as environment variables.

## 12. Limits

* The firmware has not been tested on real hardware yet.
* The database is one file inside a Docker volume, so run one copy of the server. Amazon RDS is the next step for more.
* HTTP only and one shared device key, which is fine for a demo.
* Fill is assumed to change only when the lid opens.

## License

MIT
