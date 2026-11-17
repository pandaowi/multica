# --- Build stage ---
FROM golang:1.27.4-alpine AS builder

RUN apk add --no-cache git

ENV GOMAXPROCS=2
WORKDIR /src

# Cache dependencies
COPY server/go.mod server/go.sum ./server/
RUN cd server && go mod download

# Copy server source
COPY server/ ./server/

# Verify provider boundaries before building the matching API and migration tools.
RUN cd server && go test -p 2 ./internal/providerprivacy ./internal/leaderrouting ./internal/realtime ./internal/middleware ./internal/handler ./cmd/server ./cmd/migrate ./internal/migrations

# Build binaries
ARG VERSION=dev
ARG COMMIT=unknown
ARG DATE=unknown
RUN cd server && CGO_ENABLED=0 go build -p 2 -ldflags "-s -w -X main.version=${VERSION} -X main.commit=${COMMIT}" -o bin/server ./cmd/server
RUN cd server && CGO_ENABLED=0 go build -p 2 -ldflags "-s -w -X main.version=${VERSION} -X main.commit=${COMMIT} -X main.date=${DATE}" -o bin/multica ./cmd/multica
RUN cd server && CGO_ENABLED=0 go build -p 2 -ldflags "-s -w" -o bin/migrate ./cmd/migrate
RUN cd server && CGO_ENABLED=0 go build -p 2 -ldflags "-s -w" -o bin/maintenance ./cmd/maintenance
RUN cd server && CGO_ENABLED=0 go build -p 2 -ldflags "-s -w" -o bin/backfill_task_usage_hourly ./cmd/backfill_task_usage_hourly
RUN cd server && CGO_ENABLED=0 go build -p 2 -ldflags "-s -w" -o bin/backfill_codex_usage_cache ./cmd/backfill_codex_usage_cache

# --- Runtime stage ---
FROM alpine:3.21

RUN apk add --no-cache ca-certificates tzdata

WORKDIR /app

COPY --from=builder /src/server/bin/server .
COPY --from=builder /src/server/bin/multica .
COPY --from=builder /src/server/bin/migrate .
COPY --from=builder /src/server/bin/maintenance .
COPY --from=builder /src/server/bin/backfill_task_usage_hourly .
COPY --from=builder /src/server/bin/backfill_codex_usage_cache .
COPY server/migrations/ ./migrations/
COPY LICENSE NOTICE ./
COPY docker/entrypoint.sh .
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["./entrypoint.sh"]
