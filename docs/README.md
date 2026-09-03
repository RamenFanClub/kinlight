# Emergency Exit

A personal digital legacy vault that helps users prepare for sudden death by recording assets, documenting final wishes, storing Will details, monitoring liveness via periodic check-ins, and notifying family members if the user stops responding.

## Architecture

This project follows **Domain-Driven Design** with **microservices**:

| Service | Port | Database | Purpose |
|---------|------|----------|---------|
| api-gateway | 8000 | — | JWT auth, rate limiting, routing |
| identity-service | 8001 | ee_identity | Users, auth, MFA |
| vault-service | 8002 | ee_vault | Assets, Will, legal docs |
| wishes-service | 8003 | ee_wishes | Wishes, scheduled messages |
| guardian-service | 8004 | ee_guardian | Kin contacts, access levels |
| pulse-service | 8005 | ee_pulse | Check-ins, alerts |
| notification-service | 8006 | — | Email, SMS, push |

## Quick Start

### Prerequisites
- Python 3.11+
- Docker & Docker Compose
- MongoDB 7.0+

### Run all services
```bash
docker compose up --build
```

### Run a single service (development)
```bash
cd vault-service
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8002
```

### Run tests
```bash
cd vault-service
pytest tests/ -v --cov=app
```

## Project Structure
```
emergency-exit/
├── api-gateway/          # Entry point, auth, routing
├── identity-service/     # User management, JWT, biometrics
├── vault-service/        # Assets, Will, legal documents
├── wishes-service/       # Wishes, messages, digital handover
├── guardian-service/     # Kin contacts, access control
├── pulse-service/        # Check-in timer, alert scheduler
├── notification-service/ # Email, SMS, push notifications
├── frontend/             # index.html (current), React (planned)
├── docs/                 # Architecture blueprint, feature register
├── docker-compose.yml    # Full stack orchestration
└── docs/reference.md     # AI assistant reference (architecture, API, conventions)
```

## Documentation
- `docs/reference.md` — Project reference for AI assistants (architecture, API, conventions)
- `docs/Emergency_Exit_Technical_Blueprint.docx` — Full architecture
- `docs/Emergency_Exit_Feature_Register.docx` — Feature tracking
