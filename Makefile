.PHONY: install backend frontend test-backend

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm install

backend:
	cd backend && ./run.sh

frontend:
	cd frontend && npm run dev

# 血缘变更影响分析的端到端校验：幂等、并发互斥、发布闸门、三个业务面同步落版。
test-backend:
	cd backend && .venv/bin/python -m tests.test_impact
