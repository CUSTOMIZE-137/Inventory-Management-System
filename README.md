# 📦 Enterprise Inventory Management System (IMS)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Status: In Development](https://img.shields.io/badge/Status-Active%20Development-orange.svg)]()
[![Contributions Welcome](https://img.shields.io/badge/Contributions-Welcome-brightgreen.svg)]()

A comprehensive, scalable web-based Inventory Management System designed to handle organizational assets, consumables, and stock movements with strict auditability, multi-location tracking, and hierarchical approval workflows.

---

## 📋 Table of Contents
1. [Overview](#-overview)
2. [Key Features](#-key-features)
3. [System Architecture & Tech Stack](#-system-architecture--tech-stack)
4. [User Roles & Permissions Matrix](#-user-roles--permissions-matrix)
5. [Database Schema Overview](#-database-schema-overview)
6. [Project Structure](#-project-structure)
7. [Getting Started & Installation](#-getting-started--installation)
8. [API Endpoints Reference](#-api-endpoints-reference)
9. [Contributing](#-contributing)
10. [License](#-license)

---

## 🌟 Overview

Managing organizational inventory efficiently requires preventing stockouts, ensuring complete accountability of high-value assets, and eliminating spreadsheet errors. This Enterprise Inventory Management System provides real-time visibility across multiple storage locations, enforces transaction safety to prevent negative stock anomalies, and automates multi-stage requisition workflows.

---

## 🚀 Key Features

* **Hierarchical Location Tracking:** Manage inventory not just at the organization level, but drill down to specific warehouses, rooms, aisles, shelves, and storage bins.
* **Multi-Stage Approval Workflows:** Route high-value asset and material requests through structured review chains (`Employee Request` to `Supervisor Review` to `Store Manager Dispatch`).
* **Role-Based Access Control (RBAC):** Granular permissions separating Requesters, Warehouse Clerks, Procurement Officers, and System Administrators.
* **Immutable Audit Trails:** Automatically log every stock movement, adjustment, and transfer with timestamps, quantity deltas, and user attribution for full organizational accountability.
* **Low-Stock Alerts & Thresholds:** Automated indicators and threshold triggers to prevent critical operational stockouts.
* **Concurrency & Transaction Safety:** Strict database transaction handling (ACID-compliant) and row-locking to prevent race conditions during simultaneous stock issuances.

---

## 🛠️ System Architecture & Tech Stack

* **Backend API:** Python (FastAPI) / Node.js (Express) ensuring high-performance asynchronous request handling.
* **Database:** PostgreSQL / MySQL relational database ensuring strict foreign key constraints and transactional integrity.
* **Frontend Dashboard:** React / Tailwind CSS responsive web interface optimized for both desktop management and mobile/tablet barcode-scanning workflows.
* **Authentication:** JWT-based secure session handling with encrypted passwords (bcrypt).

---

## 👥 User Roles & Permissions Matrix

| Role | Core Capabilities & Permissions |
| :--- | :--- |
| **General Employee / Requester** | View catalog items, submit stock/asset requisition requests, track request approval status. |
| **Warehouse Clerk** | Process incoming shipments, execute stock check-outs/issuances, perform internal stock transfers. |
| **Supervisor / Manager** | Review, approve, or reject departmental asset requisitions and high-value requests. |
| **System Administrator** | Manage user accounts, configure master catalog categories, monitor global audit logs, and oversee system settings. |

---

## 🗄️ Database Schema Overview

The system relies on a relational schema designed around the following core entities:
* **`users`**: Stores user credentials, profiles, and assigned RBAC roles.
* **`locations`**: Hierarchical storage locations (warehouses, rooms, shelves, bins).
* **`categories`**: Groupings for catalog items (e.g., IT Hardware, Consumables, Office Supplies).
* **`items`**: Master catalog defining items, SKUs, unit costs, and minimum stock thresholds.
* **`stock_levels`**: Dynamic quantity tracking linking items to specific storage locations.
* **`transactions`**: Immutable audit logs recording inflows, outflows, transfers, and adjustments.
* **`requisitions`**: Tracks multi-stage approval workflows for item requests.

---

## 📁 Project Structure

```text
inventory-management-system/
├── backend/                  # Backend API service
│   ├── app/
│   │   ├── models/           # ORM database models & schemas
│   │   ├── routes/           # API endpoints (Auth, Inventory, Requests)
│   │   ├── services/         # Business logic and transaction safety handlers
│   │   └── main.py           # Application entry point
│   ├── requirements.txt      # Python dependencies
│   └── Dockerfile            # Backend container configuration
├── frontend/                 # User interface application
│   ├── src/
│   │   ├── components/       # Reusable UI widgets and layout elements
│   │   ├── pages/            # Dashboard, Catalog, and Requisition views
│   │   └── App.jsx           # Main application router
│   └── package.json          # Frontend dependencies
├── docs/                     # Architecture diagrams and API documentation
└── README.md
```

---

## ⚙️ Getting Started & Installation

### Prerequisites
* Python 3.10+ or Node.js 18+
* PostgreSQL or MySQL installed and running locally or via Docker

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/inventory-management-system.git
cd inventory-management-system
```

### 2. Backend Setup & Configuration
```bash
cd backend
python -m venv venv
# Activate virtual environment:
# On Windows: venv\Scripts\activate
# On macOS/Linux: source venv/bin/activate

pip install -r requirements.txt
```

Create a `.env` file in the `backend/` directory:
```env
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your_secure_password
DB_NAME=inventory_db
JWT_SECRET=your_jwt_secret_key
```

### 3. Run Database Migrations & Start Server
```bash
# Run database setup or migration scripts
# Start the backend development server
uvicorn app.main:app --reload
```

### 4. Frontend Setup & Run
```bash
cd ../frontend
npm install
npm run dev
```

---

## 🔌 API Endpoints Reference

| Method | Endpoint | Description | Access Role |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/auth/login` | Authenticate user and issue JWT token | Public |
| `GET` | `/api/items` | Retrieve master item catalog with stock counts | All Users |
| `POST` | `/api/items` | Add a new item to the catalog | Admin / Procurement |
| `POST` | `/api/transactions/issue` | Check out / issue stock items (Inbound/Outbound) | Warehouse Clerk |
| `POST` | `/api/requisitions` | Submit a new multi-stage asset request | General Employee |
| `PUT` | `/api/requisitions/{id}/approve` | Advance or approve an active requisition | Supervisor / Manager |

---

## 🤝 Contributing

Contributions, feature requests, and bug reports are welcome! 
1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📄 License

Distributed under the [MIT License](LICENSE). See `LICENSE` for more information.
