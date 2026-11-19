# SmartBin

Event-driven smart bin monitoring for a public market. Each bin measures how full it is only when something happens, and a cloud service turns those readings into alerts, a forecast and a collection route.

* **IoT:** ESP32 with an ultrasonic sensor and a lid switch
* **Cloud:** a Docker container on AWS EC2 (Kubernetes demo included) with a MySQL database on Amazon RDS

```
ESP32 bin  --- HTTP --->  AWS EC2 (Docker container)  --->  MySQL (Amazon RDS)
(sensor + lid switch)     API + dashboard                   readings, bins, alerts
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
├── backend/        cloud service (API, MySQL layer, tests)
│   ├── app/
│   └── tests/
├── firmware/       IoT device code
│   ├── esp32_smartbin/   real ESP32 board
│   └── wokwi/            online simulator
├── Dockerfile      builds the image (backend + frontend)
├── .env.example    settings template (keys and MySQL connection)
└── README.md       this file (every step and command)
```

## 3. Run it on your computer

Works the same on Windows, Ubuntu and macOS. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine on Ubuntu) and run these from the project root.

The app stores everything in MySQL, so start a MySQL container first. Use the same password here and for `DB_PASSWORD` in `.env`.

```
cp .env.example .env
docker network create smartbin-net
docker run -d --name smartbin-db --network smartbin-net -e MYSQL_ROOT_PASSWORD=change-me-db-password -e MYSQL_DATABASE=smartbin -v smartbin-mysql:/var/lib/mysql -p 3306:3306 mysql:8.0
docker build -t smartbin:v1 .
docker run -d --name smartbin --network smartbin-net -p 8000:8000 --env-file .env smartbin:v1
```

(Windows Command Prompt: use `copy` instead of `cp`. PowerShell accepts `cp`.)

MySQL needs about 30 seconds to start the first time. The app keeps retrying until it can connect, so you do not have to wait. Check it with `docker logs smartbin`.

Open http://localhost:8000. If you turned on `PROTECT_DASHBOARD` or set an `ADMIN_TOKEN`, paste the admin token in the box at the top.

The tables are created automatically on first start.

Stop and remove everything:

```
docker rm -f smartbin smartbin-db
docker network rm smartbin-net
docker volume rm smartbin-mysql
```

Without Docker for the app (Python 3.10+, MySQL still in Docker as above). On Windows use `python`, on Ubuntu use `python3`. First set the connection values in your terminal:

```
# Ubuntu / macOS
export DB_HOST=localhost DB_USER=root DB_PASSWORD=change-me-db-password DB_NAME=smartbin
# Windows Command Prompt
set DB_HOST=localhost
set DB_USER=root
set DB_PASSWORD=change-me-db-password
set DB_NAME=smartbin
```

```
cd backend
python3 -m pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The dashboard starts empty. A bin appears when a device sends its first reading, either the Wokwi demo or a real ESP32 (see [section 8](#8-device-firmware-wokwi-and-real-esp32)).

### Look at the data with MySQL Workbench

Create a connection with hostname `localhost`, port `3306`, user `root` and your password. Then run:

```sql
USE smartbin;
SHOW TABLES;
SELECT * FROM readings ORDER BY id DESC LIMIT 20;
SELECT * FROM alerts ORDER BY id DESC;
```

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

The tests use a temporary SQLite file, so you do not need a MySQL server to run them.

```
cd backend
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
```

## 6. Deploy on AWS EC2

Same flow as the lecture practical: launch an EC2 server, open the ports, install software, run the app, optionally add a domain with Route 53. Differences from the lecture: we install Docker and run the app in a container instead of installing Apache, and the data is stored in a MySQL database on Amazon RDS.

### Part 1: Launch the EC2 server

1. AWS Console, open **EC2**, then **Launch instance**.
2. Name: `smartbin-server`
3. AMI: **Amazon Linux 2023**
4. Instance type: `t2.micro` (free tier). Use `t3.micro` if `t2.micro` is not offered in your region.
5. Key pair: create one (RSA) and choose the **.ppk** format (PuTTY). Keep the file private and never put it in the project folder. You do not need it if you connect with the browser button in Part 3.
6. Security group, allow:
   * **SSH (22)** from **Anywhere (0.0.0.0/0)**. The browser Connect button (EC2 Instance Connect) reaches your server from AWS's own addresses, not yours, so "My IP" makes it time out. This is fine for a short lab. If you only use PuTTY, "My IP" is safer.
   * **HTTP (80)** from **Anywhere (0.0.0.0/0)**. The Wokwi simulator runs on the internet, so it needs to reach port 80.
7. Launch the instance and copy its **Public IPv4 address**.

### Part 2: Create the MySQL database on RDS

1. AWS Console, open **RDS**, then **Create database**.
2. Choose **Standard create** and engine **MySQL**.
3. Template: **Free tier** if your account offers it, otherwise **Dev/Test**.
4. DB instance identifier: `smartbin-db`
5. Master username: `admin`. Set a master password and keep it safe.
6. Instance class: `db.t3.micro` (or the smallest one offered).
7. Connectivity: choose **Connect to an EC2 compute resource** and select your `smartbin-server` instance. AWS then creates the security group rules so the server can reach the database.
8. **Public access**: choose **Yes** if you want to open it from MySQL Workbench on your laptop. Choose **No** for a private database.
9. Open **Additional configuration** and set **Initial database name** to `smartbin`.
10. Click **Create database** and wait until the status is **Available**. Copy the **Endpoint**, which looks like `smartbin-db.xxxxxx.ap-south-1.rds.amazonaws.com`.

To use MySQL Workbench from your laptop, open the RDS security group, add an inbound rule **MySQL/Aurora (3306)** with source **My IP**, and create a Workbench connection with the endpoint as hostname, port `3306`, user `admin` and your password.

RDS keeps charging while it exists. When you are finished, stop it or delete it in the RDS console.

### Part 3: Connect and install Docker

Select the instance, click **Connect** in the EC2 console and use **EC2 Instance Connect**. It opens a terminal in your browser, so it works the same on Windows, Linux and macOS. (PuTTY with your `.ppk` file also works.)

```
sudo yum install docker git -y
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user
exit
```

Connect again so the group change applies.

### Part 4: Get the code and run it

```
git clone https://github.com/YOUR_USERNAME/smartbin.git
cd smartbin
cp .env.example .env
nano .env
```

In `.env` set:

* two different random values for `API_KEY` and `ADMIN_TOKEN`, and `PROTECT_DASHBOARD=true`
* `DB_HOST` = your RDS endpoint
* `DB_USER=admin` and `DB_PASSWORD` = your RDS master password
* `DB_NAME=smartbin`

Save with Ctrl+O, Enter, Ctrl+X.

Build and run:

```
docker build -t smartbin:v1 .
docker run -d --name smartbin -p 80:8000 --env-file .env --restart unless-stopped smartbin:v1
docker ps
```

Check on the server:

```
curl http://localhost/health
```

You should see `{"status":"ok"}`. If the container keeps stopping, run `docker logs smartbin`. A "Cannot connect to MySQL" message means the `DB_` values are wrong or the RDS security group does not allow your EC2 server on port 3306.

### Part 5: Open the dashboard

Open `http://<EC2-PUBLIC-IP>/` in your browser (http, not https) and paste the admin token in the box at the top.

The dashboard starts empty. A bin appears when a device sends its first reading. Use the Wokwi demo (section 8) and set `SERVER` to `http://<EC2-PUBLIC-IP>` and `API_KEY` to the value in your `.env`.

### Part 6 (optional): Custom domain with Route 53

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
docker run -d --name smartbin -p 80:8000 --env-file .env --restart unless-stopped smartbin:v1
```

Stored readings stay in MySQL, so nothing is lost when you rebuild the container. To start with clean data, open MySQL Workbench and run `DROP DATABASE smartbin;`. The app creates the tables again when it restarts.

### When you are finished

Stop the EC2 instance and stop or delete the RDS database so you are not charged.

## 7. Run on Kubernetes (Docker Desktop)

Same method as the lecture: enable Kubernetes in Docker Desktop, then use `kubectl` commands. Works on Windows, Ubuntu and macOS.

The app needs a MySQL database. For this demo, start the MySQL container from [section 3](#3-run-it-on-your-computer) (the `smartbin-db` container with port 3306 published). The pod will reach it through `host.docker.internal`, which points to your computer. You can also use your RDS endpoint instead.

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

The pod does not read your `.env` file, so give it the settings (this restarts the pod). Without them the app cannot reach the database.

```
kubectl set env deployment/smartbin API_KEY=your-device-key ADMIN_TOKEN=your-admin-token DB_HOST=host.docker.internal DB_USER=root DB_PASSWORD=change-me-db-password DB_NAME=smartbin
kubectl get pods
kubectl logs deployment/smartbin
```

Use your RDS endpoint, `admin` and the RDS password instead if you are using RDS.

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

Kubernetes creates a new pod by itself. Your readings are still there, because they are stored in MySQL and not inside the pod.

### Scaling demo

```
kubectl scale deployment smartbin --replicas=3
kubectl get pods
```

All copies share the same MySQL database, so they show the same data. Scale back with `--replicas=1`.

### Clean up

```
kubectl delete service smartbin
kubectl delete deployment smartbin
```

Remove the MySQL container as shown in section 3 when you no longer need it.

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
| `DB_HOST` | MySQL server: your RDS endpoint, or `smartbin-db` locally |
| `DB_PORT` | MySQL port, default `3306` |
| `DB_USER` | MySQL user (`admin` on RDS, `root` locally) |
| `DB_PASSWORD` | MySQL password |
| `DB_NAME` | database name, default `smartbin` (created if missing) |

Advanced values (thresholds, bin depth, cooldown) have defaults in `backend/app/config.py` and can also be set as environment variables.

## 12. Limits

* The firmware has not been tested on real hardware yet.
* One shared device key and HTTP only, which is fine for a demo.
* The RDS database is a single instance. Multi-AZ would add a standby copy but is not set up here.
* Several server copies share one database. Two readings arriving at the same instant could very rarely create a duplicate alert.
* Fill is assumed to change only when the lid opens.

## License

MIT
