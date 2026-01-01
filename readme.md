# 🤖 Bitkub Pro Bot (Async & Real-time)

A high-performance, asynchronous cryptocurrency trading bot for **Bitkub Exchange**. Built with **FastAPI** and **Python**, featuring a real-time **WebSocket dashboard** for monitoring and managing trades.

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.68+-green.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

## ✨ Features

* **⚡ High Performance:** Fully asynchronous architecture using `asyncio` and `httpx`. Can monitor 20+ symbols simultaneously with minimal latency.
* **📊 Technical Analysis:** Built-in indicators: **RSI**, **MACD**, **Bollinger Bands**, and **Stochastic Oscillator**.
* **🖥️ Real-time Dashboard:**
    * Live logs via WebSocket (No refresh needed).
    * Dark mode UI inspired by trading platforms.
    * Responsive design (Tailwind CSS).
* **🛡️ Security:** Password-protected login system (Cookie-based authentication).
* **📝 CRUD Watchlist:** Add, Edit, and Delete watched symbols directly from the UI.
* **💾 Persistent Data:** SQLite database to store trading history and configuration.
* **🚀 Production Ready:** configured for deployment with Nginx and Systemd.

## 🛠️ Tech Stack

* **Backend:** Python, FastAPI, Uvicorn, Httpx, SQLite
* **Frontend:** HTML5, Vanilla JavaScript, Tailwind CSS (CDN), FontAwesome
* **Deployment:** Ubuntu, Nginx (Reverse Proxy), Systemd, Certbot (SSL)

## 📂 Project Structure

```bash
├── main.py              # Entry point (FastAPI server & WebSocket manager)
├── bot_engine.py        # Core trading logic & Async loop
├── bitkub.py            # Async wrapper for Bitkub API
├── database.py          # SQLite database management
├── indicators.py        # Technical analysis formulas
├── dashboard.html       # Main UI (SPA)
├── login.html           # Login page
├── .env                 # Environment variables (Sensitive data)
└── requirements.txt     # Python dependencies

```

## 🚀 Installation (Local Development)

1. **Clone the repository:**
```bash
git clone [https://github.com/yourusername/bitkub-pro-bot.git](https://github.com/yourusername/bitkub-pro-bot.git)
cd bitkub-pro-bot

```


2. **Create a Virtual Environment:**
```bash
python -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate

```


3. **Install Dependencies:**
```bash
pip install -r requirements.txt

```


4. **Configuration:**
Create a `.env` file in the root directory:
```env
API_KEY=your_bitkub_api_key
API_SECRET=your_bitkub_api_secret
BASE_URL=[https://api.bitkub.com](https://api.bitkub.com)
BOT_PASSWORD=your_secure_password

```


5. **Run the Bot:**
```bash
python main.py

```


Access the dashboard at: `http://localhost:8000`

## 🌐 Deployment (Ubuntu Server + Nginx)

To deploy this bot on a production server (e.g., DigitalOcean, AWS) with HTTPS:

1. **Setup Systemd Service:**
Create `/etc/systemd/system/bitkub.service`:
```ini
[Unit]
Description=Bitkub Bot Service
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/bitkub_bot
ExecStart=/home/ubuntu/bitkub_bot/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target

```


2. **Setup Nginx Reverse Proxy:**
Create `/etc/nginx/sites-available/bitkub`:
```nginx
server {
    server_name your-domain.com;

    location / {
        proxy_pass [http://127.0.0.1:8000](http://127.0.0.1:8000);
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /ws {
        proxy_pass [http://127.0.0.1:8000](http://127.0.0.1:8000);
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}

```


3. **Enable and Start:**
```bash
sudo ln -s /etc/nginx/sites-available/bitkub /etc/nginx/sites-enabled/
sudo systemctl restart nginx
sudo systemctl start bitkub

```


4. **SSL Certificate:**
```bash
sudo certbot --nginx -d your-domain.com

```



## ⚠️ Disclaimer

This software is for **educational purposes only**. Cryptocurrency trading involves significant risk. The author is not responsible for any financial losses incurred while using this bot. Please test with small amounts first.

---

Developed by e29ckg

```

```