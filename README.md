# SmartBin

Event-driven smart bin monitoring for a public market. Each bin measures how full it is only when something happens, and a cloud service turns those readings into alerts, a forecast and a collection route.

* **IoT:** ESP32 with an ultrasonic sensor and a lid switch
* **Cloud:** one Docker container on AWS EC2 with a SQLite database file on a Docker volume

```
ESP32 bin  --- HTTP --->  AWS EC2 (Docker container)
(sensor + lid switch)     API + dashboard + SQLite file
                                ^
                                |
                             browser
```

## Contents

1. [Why event-driven](#1-why-event-driven)
2. [Project structure](#2-project-structure)
3. [Run it on your computer](#3-run-it-on-your-computer)
4. [Send a test reading](#4-send-a-test-reading)
5. [Run the tests](#5-run-the-tests)
6. [Deploy on AWS EC2](#6-deploy-on-aws-ec2)
7. [Device firmware (Wokwi and real ESP32)](#7-device-firmware-wokwi-and-real-esp32)
8. [How it works](#8-how-it-works)
9. [API](#9-api)
10. [Settings](#10-settings)
11. [Limits](#11-limits)

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
├── backend/        cloud service (API, SQLite layer, tests)
│   ├── app/
│   └── tests/
├── firmware/       IoT device code
│   ├── esp32_smartbin/   real ESP32 board
│   └── wokwi/            online simulator
├── Dockerfile      builds the image (backend + frontend)
├── .env.example    settings template (keys)
└── README.md       this file (every step and command)
```

## 3. Run it on your computer

Works on Ubuntu, macOS and Windows. Install [Docker Engine](https://docs.docker.com/engine/install/ubuntu/) (or Docker Desktop) and run these from the project root.

```
cp .env.example .env
docker build -t smartbin:v1 .
docker run -d --name smartbin -p 8000:8000 --env-file .env -v smartbin-data:/data smartbin:v1
```

The database is a single SQLite file in the `smartbin-data` volume, so readings survive rebuilds. The tables are created on first start. Check it with `docker logs smartbin`.

Open http://localhost:8000. If you turned on `PROTECT_DASHBOARD` or set an `ADMIN_TOKEN`, paste the admin token in the box at the top.

Stop and remove everything:

```
docker rm -f smartbin
docker volume rm smartbin-data
```

Without Docker (Python 3.10+, use `python` on Windows):

```
cd backend
python3 -m pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The database is then `backend/data/smartbin.db`. Set `DB_PATH` to put it somewhere else.

The dashboard starts empty. A bin appears when a device sends its first reading, either the Wokwi demo or a real ESP32 (see [section 7](#7-device-firmware-wokwi-and-real-esp32)).

### Look at the data

```
docker exec -it smartbin python -c "import sqlite3; c=sqlite3.connect('/data/smartbin.db'); print(c.execute('select id, bin_id, fill_pct, trigger from readings order by id desc limit 10').fetchall())"
```

Or copy the file out with `docker cp smartbin:/data/smartbin.db .` and open it in DB Browser for SQLite.

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

The tests use a temporary SQLite file, so they need nothing else running.

```
cd backend
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
```

## 6. Deploy on AWS EC2

Launch an EC2 server, open the ports, install Docker, run the app in a container. The data lives in a SQLite file on a Docker volume on the same server, so there is no separate database to create.

### Part 1: Launch the EC2 server

1. AWS Console, open **EC2**, then **Launch instance**.
2. Name: `smartbin-server`
3. AMI: **Amazon Linux 2023**
4. Instance type: `t2.micro` (free tier). Use `t3.micro` if `t2.micro` is not offered in your region.
5. Key pair: choose **Proceed without a key pair**. You connect with the browser button in Part 2, so no key file is needed.
6. Security group, allow:
   * **SSH (22)** from **Anywhere (0.0.0.0/0)**. The browser Connect button (EC2 Instance Connect) reaches your server from AWS's own addresses, not yours, so "My IP" makes it time out. This is fine for a short demo.
   * **HTTP (80)** from **Anywhere (0.0.0.0/0)**. The Wokwi simulator runs on the internet, so it needs to reach port 80.
7. Launch the instance and copy its **Public IPv4 address**.

### Part 2: Connect and install Docker

Select the instance, click **Connect** in the EC2 console and use **EC2 Instance Connect**. It opens a terminal in your browser.

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
git clone https://github.com/YOUR_USERNAME/smart-bin.git
cd smart-bin
cp .env.example .env
nano .env
```

In `.env` set two different random values for `API_KEY` and `ADMIN_TOKEN`, and set `PROTECT_DASHBOARD=true`. Make a random value with `openssl rand -hex 16`. Save with Ctrl+O, Enter, Ctrl+X.

Build and run:

```
docker build -t smartbin:v1 .
docker run -d --name smartbin -p 80:8000 --env-file .env -v smartbin-data:/data --restart unless-stopped smartbin:v1
docker ps
curl http://localhost/health
```

You should see `{"status":"ok"}`. If the container keeps stopping, run `docker logs smartbin`.

### Part 4: Open the dashboard

Open `http://<EC2-PUBLIC-IP>/` in your browser (http, not https) and paste the admin token in the box at the top.

The dashboard starts empty. A bin appears when a device sends its first reading. Use the Wokwi demo (section 7) and set `SERVER` to `http://<EC2-PUBLIC-IP>` and `API_KEY` to the value in your `.env`.

### Update after a code change

If the server still runs the MySQL version, see the move steps below first. Otherwise:

```
cd smart-bin
git pull
docker build -t smartbin:v1 .
docker rm -f smartbin
docker run -d --name smartbin -p 80:8000 --env-file .env -v smartbin-data:/data --restart unless-stopped smartbin:v1
```

Readings live in the `smartbin-data` volume, so nothing is lost when you rebuild the container. To start with clean data run `docker rm -f smartbin && docker volume rm smartbin-data`, then the last `docker run` again.

### Move an EC2 server from the MySQL version

```
cd smart-bin
git pull
nano .env                    # delete the DB_ lines (optional, they are ignored now)
docker rm -f smartbin
docker build -t smartbin:v2 .
docker run -d --name smartbin -p 80:8000 --env-file .env -v smartbin-data:/data --restart unless-stopped smartbin:v2
curl http://localhost/health
```

The old readings stay in RDS and are not copied over, so the dashboard starts fresh. Once it works, stop or delete the RDS database in the RDS console to stop the charges.

### Back up the database

```
docker cp smartbin:/data/smartbin.db ~/smartbin-backup.db
```

### When you are finished

Stop the EC2 instance so you are not charged. The volume is kept, but the public IP changes after a stop and start unless you attach an Elastic IP. Update `SERVER` in the firmware if the address changes.

## 7. Device firmware (Wokwi and real ESP32)

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

## 8. How it works

* **Fill %** = (depth - distance) / (depth - dead zone) x 100, clamped to 0..100. Impossible distances raise a sensor fault.
* **Alerts** at 80% (nearly full) and 95% (full). They clear below 70%, or when a collection is detected.
* **Collection detection** is a drop of 30 points or more between two readings.
* **Forecast** is a straight line fitted to recent readings since the last collection.
* **Route** orders bins full, then nearly full, then soonest to overflow.
* **Refresh** keeps one request open, so the device gets the command instantly. There is a 15 s cooldown per bin.
* **Offline detection** raises an alert after 150 minutes of silence.

## 9. API

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

## 10. Settings

Set these in `.env`.

| Variable | Meaning |
|---|---|
| `API_KEY` | key the devices send with every reading |
| `ADMIN_TOKEN` | token for operator actions |
| `PROTECT_DASHBOARD` | `true` makes the dashboard need the token |
| `DB_PATH` | SQLite file, default `./data/smartbin.db` (`/data/smartbin.db` in Docker) |

Advanced values (thresholds, bin depth, cooldown) have defaults in `backend/app/config.py` and can also be set as environment variables.

## 11. Limits

* The firmware has not been tested on real hardware yet.
* One shared device key and HTTP only, which is fine for a demo.
* SQLite suits one server. Run a single container, because several copies cannot share one file safely.
* Fill is assumed to change only when the lid opens.

## License

MIT
