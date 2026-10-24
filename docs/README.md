# Tài liệu FlowBridge

| Tài liệu | Nội dung |
|---|---|
| [Architecture](ARCHITECTURE.md) | Ranh giới module, dữ liệu, quyền và tích hợp |
| [Development](DEVELOPMENT.md) | Môi trường phát triển, contracts và tiêu chí tích hợp |
| [Repository](REPOSITORY.md) | Nội dung được version-control và nội dung cục bộ |
| [Declarative Onboarding](ONBOARDING.md) | Manifest v1, schema discovery, relationship compiler, CLI và Core/API integration contracts |
| [API Contracts](API-CONTRACTS.md) | OpenAPI 3.1.1, P0 Data Sharing/P1 boundaries, canonical Data Contract, validation và DB alignment gaps |
| [Core Backend P0](CORE-P0.md) | Runtime handlers, services/policy/worker, offline validation và PostgreSQL/Hasura integration checklist |
| [Automated Onboarding](AUTOMATED-ONBOARDING.md) | PostgreSQL CoreStore, native API/worker, integration evidence và E2E blockers |
| [Docker Integration — profile cũ](DOCKER.md) | Hướng dẫn và bằng chứng nghiệm thu Keycloak/Hasura với Supabase; demo hiện hành dùng PostgreSQL local |
| [Database Setup](database/README.md) | Core P0 một doanh nghiệp, migrations theo ownership, guarded reset và giới hạn tích hợp runtime |
| [Contributing](../CONTRIBUTING.md) | Quy trình phát triển, kiểm tra và review |

Hướng dẫn build/test tại [README gốc](../README.md); tài liệu component nằm cạnh source tương ứng. Tài liệu mô tả rõ kiến trúc đích và trạng thái implementation hiện tại.
