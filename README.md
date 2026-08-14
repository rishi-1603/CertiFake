<div align="center">
  <img src="https://img.shields.io/badge/Status-Active-success.svg" alt="Status" />
  <img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License" />
  <h1>🛡️ CertiFake Pro</h1>
  <p><strong>Advanced AI Certificate Intelligence & Forensics</strong></p>
</div>

<br />

**CertiFake Pro** is a distributed, full-stack microservices architecture designed to detect counterfeit professional certificates and credentials. It uses OCR text extraction (Tesseract) and image forensics — Error Level Analysis, edge-density checks, and keyword validation — to generate a detailed authenticity score.

## 🚀 Key Features

*   **Asynchronous Processing**: Uploads are queued via **Apache Kafka** and processed by dedicated forensic Python workers.
*   **AI Forensics & OCR**: Utilizes `tesseract.js` and `OpenCV` to perform deep pixel-level analysis and text extraction.
*   **Real-time Dashboard**: A sleek, dark-themed **React + Vite** frontend providing real-time feedback and dynamic forensic heatmaps.
*   **Robust Storage**: Stores binary objects locally/securely using **MinIO** (S3 compatible) and structured data in **PostgreSQL**.
*   **Observability**: Integrated with **Prometheus** and **Grafana** for deep system health monitoring.

---

## 🏗️ Architecture Stack

### Frontend
*   **React 19** powered by **Vite**
*   Vanilla CSS (Modern Custom Properties, Flexbox, Micro-animations)
*   Deployed on Vercel

### Backend Microservices
*   **FastAPI**: API Gateway and Asynchronous Workers
*   **PostgreSQL 15**: Relational Database for analysis records
*   **Redis 7**: Fast caching layer
*   **Kafka & Zookeeper**: Distributed event streaming platform
*   **MinIO**: High-performance Object Storage

---

## 💻 Local Development

Running the entire architecture locally is incredibly easy thanks to Docker Compose.

### 1. Start the Backend Infrastructure
Ensure you have Docker and Docker Compose installed.

```bash
git clone https://github.com/rishi-1603/CertiFake.git
cd CertiFake
docker-compose up -d --build
```
*This command will pull and start all 10 services including the API gateway, Postgres, Kafka, and the Python forensic workers.*

### 2. Start the Frontend
In a new terminal window, navigate to the frontend directory:

```bash
cd CertiFake/frontend
npm install
npm run dev
```

Visit `http://localhost:5173` to access the CertiFake UI! 
*(The backend API is automatically exposed on `http://localhost:8000`)*

---

## 📊 Monitoring

When running via Docker Compose, you can access the system monitoring tools:
*   **Grafana Dashboard**: `http://localhost:3000` *(Default Login: admin / admin)*
*   **Prometheus**: `http://localhost:9090`
*   **MinIO Console**: `http://localhost:9001` *(Default Login: minioadmin / minioadmin)*

---

## 🌐 Production Deployment

For production, it is highly recommended to split the infrastructure:
1.  **Frontend**: Deploy the `frontend/` directory to **Vercel** or **Netlify**.
2.  **Backend**: Deploy the `docker-compose.yml` stack to a single high-performance VM (AWS EC2, DigitalOcean) or utilize the provided `k8s/` manifests to deploy to a Kubernetes cluster.

---

*Built with ❤️ for maintaining professional credential integrity.*
