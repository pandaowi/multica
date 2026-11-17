ARG PRIVACY_BUILD_BASE=golang:1.27.3-alpine
FROM ${PRIVACY_BUILD_BASE} AS privacy-builder
ENV GOMAXPROCS=2
WORKDIR /src/server
COPY server/go.mod server/go.sum ./
RUN go mod download
COPY server/ ./
RUN go test -p 2 ./internal/providerprivacy ./internal/leaderrouting ./internal/realtime ./internal/middleware
RUN go test -p 2 ./internal/handler ./cmd/server
ARG MULTICA_SOURCE_REV=unknown
RUN CGO_ENABLED=0 go build -p 2 -ldflags "-s -w -X main.version=panda-platform-20261117 -X main.commit=${MULTICA_SOURCE_REV}" -o /out/server ./cmd/server

FROM ghcr.io/multica-ai/multica-backend@sha256:74e7bdddef780b925652f4518f18f54ab790bdfe1ec7803b4dac2019ee2525be
COPY --from=privacy-builder /out/server /app/server
LABEL id.panda.provider-privacy="3"
