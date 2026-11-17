// Package providerprivacy projects provider-controlled operations into a
// customer-safe API. Its configuration is server-owned, never tenant-editable.
package providerprivacy

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"regexp"
	"strings"
	"sync"
)

type Config struct {
	WorkspaceID         string   `json:"workspace_id"`
	OperatorUserIDs     []string `json:"operator_user_ids"`
	InternalSkillIDs    []string `json:"internal_skill_ids"`
	ManagedAgentIDs     []string `json:"managed_agent_ids"`
	ManagedSquadIDs     []string `json:"managed_squad_ids"`
	ManagedRuntimeIDs   []string `json:"managed_runtime_ids"`
	PrivateStrings      []string `json:"private_strings"`
	PublicAttachmentIDs []string `json:"public_attachment_ids"`
	PublicSkillIDs      []string `json:"public_skill_ids"`
}

type Policy struct{ config Config }
type Identity struct{ UserID, WorkspaceID string }

func New(c Config) *Policy { return &Policy{config: c} }

var once sync.Once
var active *Policy

// Default fails startup closed when an explicitly configured policy is invalid.
func Default() *Policy {
	once.Do(func() {
		path := os.Getenv("MULTICA_PROVIDER_PRIVACY_CONFIG")
		if path == "" {
			active = New(Config{})
			return
		}
		body, err := os.ReadFile(path)
		if err != nil {
			panic("provider privacy configuration unavailable")
		}
		var config Config
		if json.Unmarshal(body, &config) != nil || config.WorkspaceID == "" || len(config.OperatorUserIDs) == 0 {
			panic("invalid provider privacy configuration")
		}
		active = New(config)
	})
	return active
}

func contains(values []string, value string) bool {
	for _, v := range values {
		if value != "" && strings.EqualFold(value, v) {
			return true
		}
	}
	return false
}
func (p *Policy) Enabled() bool { return p != nil && p.config.WorkspaceID != "" }
func (p *Policy) IsOperator(user string) bool {
	return p.Enabled() && contains(p.config.OperatorUserIDs, user)
}
func (p *Policy) Restricted(user, workspace string) bool {
	return p.Enabled() && !p.IsOperator(user) && (workspace == "" || workspace == p.config.WorkspaceID)
}

func (p *Policy) privateSkill(id string) bool   { return contains(p.config.InternalSkillIDs, id) }
func (p *Policy) managedAgent(id string) bool   { return contains(p.config.ManagedAgentIDs, id) }
func (p *Policy) managedSquad(id string) bool   { return contains(p.config.ManagedSquadIDs, id) }
func (p *Policy) managedRuntime(id string) bool { return contains(p.config.ManagedRuntimeIDs, id) }

func (p *Policy) ProviderOwned(workspace, owner string) bool {
	return p.Enabled() && workspace == p.config.WorkspaceID && p.IsOperator(owner)
}

func (p *Policy) PrivateSkillResource(workspace, id, creator string) bool {
	return p.Enabled() && workspace == p.config.WorkspaceID && (p.privateSkill(id) || p.IsOperator(creator) && !contains(p.config.PublicSkillIDs, id))
}

func (p *Policy) skillObject(v map[string]any) bool {
	id, _ := v["id"].(string)
	creator, _ := v["created_by"].(string)
	_, hasConfig := v["config"]
	_, hasContent := v["content"]
	_, hasName := v["name"]
	return p.privateSkill(id) || (hasName && (hasConfig || hasContent) && p.IsOperator(creator) && !contains(p.config.PublicSkillIDs, id))
}

// Operator-generated artifacts may contain saved raw execution logs. A public
// release is explicit and server-owned; customer uploads retain existing ACLs.
func (p *Policy) PrivateArtifact(workspace, id, uploaderType, uploaderID string) bool {
	return p.Enabled() && workspace == p.config.WorkspaceID && !contains(p.config.PublicAttachmentIDs, id) && (uploaderType == "agent" || p.IsOperator(uploaderID))
}

// Deny uses decoded URL path segments, not client-supplied actor/role headers.
func (p *Policy) Deny(user, workspace, method, path string, body []byte) bool {
	if !p.Restricted(user, workspace) {
		return false
	}
	parts := strings.Split(strings.Trim(path, "/"), "/")
	if len(parts) < 2 || parts[0] != "api" {
		return false
	}
	if parts[1] == "search-index" {
		return true
	} // opaque snapshots bypass JSON projection
	if parts[1] == "tasks" && len(parts) > 3 && (parts[3] == "context-receipt") {
		return true
	}
	if parts[1] == "squads" && len(parts) > 2 && p.managedSquad(parts[2]) {
		return true
	}
	if parts[1] == "runtimes" && len(parts) > 3 && p.managedRuntime(parts[2]) {
		return true
	}
	for _, part := range parts {
		if p.privateSkill(part) {
			return true
		}
	}
	if parts[1] == "agents" && len(parts) > 3 && p.managedAgent(parts[2]) && (parts[3] == "env" || parts[3] == "mcp-servers" || parts[3] == "apps" || parts[3] == "runtime-skills") {
		return true
	}
	write := method != "GET" && method != "HEAD" && method != "OPTIONS"
	if len(parts) > 2 && write && (parts[1] == "agents" && p.managedAgent(parts[2]) || parts[1] == "squads" && p.managedSquad(parts[2])) {
		// Cancelling one's own task has its own ACL and does not reconfigure an agent.
		return true
	}
	if write && len(body) > 0 && (parts[1] == "agents" || parts[1] == "agent-builder" || parts[1] == "squads" || parts[1] == "skills") {
		var data any
		if json.Unmarshal(body, &data) == nil && (p.referencesManagedRuntime(data) || p.referencesPrivateSkill(data)) {
			return true
		}
	}
	return false
}

// Block binding/copying a provider skill into a customer's own runnable agent.
func (p *Policy) referencesPrivateSkill(value any) bool {
	switch v := value.(type) {
	case string:
		return p.privateSkill(v)
	case map[string]any:
		for _, x := range v {
			if p.referencesPrivateSkill(x) {
				return true
			}
		}
	case []any:
		for _, x := range v {
			if p.referencesPrivateSkill(x) {
				return true
			}
		}
	}
	return false
}

func (p *Policy) referencesManagedRuntime(value any) bool {
	switch v := value.(type) {
	case map[string]any:
		if id, ok := v["runtime_id"].(string); ok && p.managedRuntime(id) {
			return true
		}
		for _, x := range v {
			if p.referencesManagedRuntime(x) {
				return true
			}
		}
	case []any:
		for _, x := range v {
			if p.referencesManagedRuntime(x) {
				return true
			}
		}
	}
	return false
}

var privatePath = regexp.MustCompile(`/(?:home|root|opt|var/run|run/user)/[^\s\x60"'<>\\]+`)
var containerName = regexp.MustCompile(`coder-ws-[a-f0-9-]+`)

func (p *Policy) text(s string) string {
	s = privatePath.ReplaceAllString(s, "[internal]")
	s = containerName.ReplaceAllString(s, "[internal]")
	for _, v := range p.config.PrivateStrings {
		if v != "" {
			s = strings.ReplaceAll(s, v, "[internal]")
		}
	}
	return s
}

// project returns false for an internal object, removing it from every list or
// nested binding. It preserves application messages and customer-owned objects.
func (p *Policy) project(value any) (any, bool) {
	switch v := value.(type) {
	case map[string]any:
		id, _ := v["id"].(string)
		if _, taskMessage := v["seq"]; taskMessage {
			if _, hasTask := v["task_id"]; hasTask {
				return p.ProgressMessage(v)
			}
		}
		if p.skillObject(v) {
			return nil, false
		}
		if uploader, ok := v["uploader_type"].(string); ok {
			workspace, _ := v["workspace_id"].(string)
			uid, _ := v["uploader_id"].(string)
			if p.PrivateArtifact(workspace, id, uploader, uid) {
				return nil, false
			}
		}
		owner, _ := v["owner_id"].(string)
		creator, _ := v["creator_id"].(string)
		_, hasInstructions := v["instructions"]
		_, hasLeader := v["leader_id"]
		if p.managedSquad(id) || hasLeader && p.IsOperator(creator) {
			// Keep list/assignment identity, with no squad configuration or members.
			out := map[string]any{"details_accessible": false, "instructions_redacted": true,
				"description": "", "instructions": "", "leader_id": "", "creator_id": "",
				"member_count": 0, "member_preview": []any{}}
			for _, key := range []string{"id", "workspace_id", "name", "avatar_url", "created_at", "updated_at", "archived_at", "archived_by"} {
				if value, ok := v[key]; ok {
					out[key], _ = p.project(value)
				}
			}
			return out, true
		}
		managed := p.managedAgent(id) || p.managedSquad(id) || hasInstructions && (p.IsOperator(owner) || p.IsOperator(creator))
		_, hasProvider := v["provider"]
		managedRuntime := p.managedRuntime(id) || hasProvider && p.IsOperator(owner)
		out := make(map[string]any, len(v))
		for key, x := range v {
			if managed && key == "skills" {
				out[key] = []any{}
				continue
			}
			if managed && (key == "instructions" || key == "system_instructions") {
				out[key] = ""
				out["instructions_redacted"] = true
				continue
			}
			switch key {
			case "work_dir", "working_directory", "prior_work_dir", "prior_session_id", "session_id", "launch_header", "device_info":
				out[key] = ""
				continue
			case "runtime_config":
				out[key] = map[string]any{}
				continue
			case "mcp_config", "custom_env":
				out[key] = nil
				continue
			case "custom_args":
				out[key] = []any{}
				continue
			}
			if managedRuntime && (key == "name" || key == "custom_name") {
				out[key] = "Managed development runtime"
				continue
			}
			if key == "runtime_name" || key == "device_name" {
				out[key] = "Managed development runtime"
				continue
			}
			// Raw task results/errors are not the final chat message. They may
			// contain shell output even when transcript endpoints are blocked.
			if _, isTask := v["agent_id"]; isTask {
				if _, hasStatus := v["status"]; hasStatus && (key == "result" || key == "error" || key == "task_prompt" || key == "execution_manifest") {
					out[key] = nil
					continue
				}
			}
			if clean, ok := p.project(x); ok {
				out[key] = clean
			}
		}
		return out, true
	case []any:
		out := make([]any, 0, len(v))
		for _, x := range v {
			if clean, ok := p.project(x); ok {
				out = append(out, clean)
			}
		}
		return out, true
	case string:
		return p.text(v), true
	default:
		return value, true
	}
}

func (p *Policy) JSON(body []byte) ([]byte, error) {
	var value any
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return nil, fmt.Errorf("unexpected trailing JSON")
	}
	clean, ok := p.project(value)
	if !ok {
		return []byte(`null`), nil
	}
	return json.Marshal(clean)
}

// ProgressMessage exposes only assistant narrative, never thinking or tool fields.
// A whitelist also handles a malformed text record carrying extra tool data.
func (p *Policy) ProgressMessage(v map[string]any) (any, bool) {
	if v["type"] != "text" {
		return nil, false
	}
	content, ok := v["content"].(string)
	if !ok || content == "" {
		return nil, false
	}
	out := map[string]any{"type": "text", "content": p.text(content), "tools_redacted": true}
	for _, key := range []string{"task_id", "issue_id", "seq", "created_at"} {
		if value, ok := v[key]; ok {
			out[key] = value
		}
	}
	return out, true
}

// Event is called at the final socket writer, including queued/replayed frames.
// No database calls or mutable per-tenant role state are used in this hot path.
func (p *Policy) Event(user, workspace string, frame []byte) ([]byte, bool) {
	if !p.Enabled() || p.IsOperator(user) {
		return frame, true
	}
	var event map[string]any
	if json.Unmarshal(frame, &event) != nil {
		if p.Restricted(user, workspace) {
			return nil, false
		}
		return frame, true
	}
	eventWorkspace, _ := event["workspace_id"].(string)
	if eventWorkspace == "" {
		if payload, ok := event["payload"].(map[string]any); ok {
			eventWorkspace, _ = payload["workspace_id"].(string)
		}
	}
	if workspace != "" && workspace != p.config.WorkspaceID && eventWorkspace != p.config.WorkspaceID {
		return frame, true
	}
	kind, _ := event["type"].(string)
	if kind == "task:message" {
		payload, ok := event["payload"].(map[string]any)
		if !ok {
			return nil, false
		}
		clean, allowed := p.ProgressMessage(payload)
		if !allowed {
			return nil, false
		}
		event["payload"] = clean
		body, err := json.Marshal(event)
		return body, err == nil
	}
	if strings.HasPrefix(kind, "skill:") && p.hasPrivateSkill(event) {
		return nil, false
	}
	clean, err := p.JSON(frame)
	return clean, err == nil
}

func (p *Policy) hasPrivateSkill(value any) bool {
	switch v := value.(type) {
	case map[string]any:
		if p.skillObject(v) {
			return true
		}
		for key, x := range v {
			if s, ok := x.(string); ok && (key == "id" || key == "skill_id") && p.privateSkill(s) {
				return true
			}
			if p.hasPrivateSkill(x) {
				return true
			}
		}
	case []any:
		for _, x := range v {
			if p.hasPrivateSkill(x) {
				return true
			}
		}
	}
	return false
}

const maxResponse = 16 << 20

type capture struct {
	header      http.Header
	status      int
	body        bytes.Buffer
	overflow    bool
	target      http.ResponseWriter
	passthrough bool
}

func (w *capture) Header() http.Header { return w.header }
func (w *capture) WriteHeader(status int) {
	if w.status != 0 {
		return
	}
	w.status = status
	kind := w.header.Get("Content-Type")
	if kind != "" && !strings.Contains(kind, "json") {
		w.passthrough = true
		for k, v := range w.header {
			w.target.Header()[k] = v
		}
		w.target.WriteHeader(status)
	}
}
func (w *capture) Write(body []byte) (int, error) {
	if w.status == 0 {
		w.WriteHeader(http.StatusOK)
	}
	if w.passthrough {
		return w.target.Write(body)
	}
	if w.body.Len()+len(body) > maxResponse {
		w.overflow = true
		return len(body), nil
	}
	return w.body.Write(body)
}

func (w *capture) Flush() {
	if w.status == 0 {
		w.WriteHeader(http.StatusOK)
	}
	if w.passthrough {
		if f, ok := w.target.(http.Flusher); ok {
			f.Flush()
		}
	}
}

// Middleware must be mounted AFTER Auth. resolve must use authenticated server
// identity and the canonical workspace resolver; never accept an operator flag.
func (p *Policy) Middleware(resolve func(*http.Request) Identity) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			who := resolve(r)
			if !p.Restricted(who.UserID, who.WorkspaceID) {
				next.ServeHTTP(w, r)
				return
			}
			var requestBody []byte
			if r.Body != nil && (strings.HasPrefix(r.URL.Path, "/api/agents") || strings.HasPrefix(r.URL.Path, "/api/agent-builder") || strings.HasPrefix(r.URL.Path, "/api/squads") || strings.HasPrefix(r.URL.Path, "/api/skills")) {
				var err error
				requestBody, err = io.ReadAll(io.LimitReader(r.Body, 2<<20+1))
				if err != nil || len(requestBody) > 2<<20 {
					http.Error(w, `{"error":"request exceeds privacy inspection limit"}`, http.StatusRequestEntityTooLarge)
					return
				}
				_ = r.Body.Close()
				r.Body = io.NopCloser(bytes.NewReader(requestBody))
			}
			if p.Deny(who.UserID, who.WorkspaceID, r.Method, r.URL.Path, requestBody) {
				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("Cache-Control", "private, no-store")
				w.WriteHeader(http.StatusForbidden)
				_, _ = w.Write([]byte(`{"error":"provider_internal_resource","message":"Detail operasional internal hanya tersedia untuk operator penyedia. Hasil aplikasi tersedia di percakapan atau issue."}`))
				return
			}
			// Ordinary file responses are left to their existing download ACLs.
			// This boundary captures JSON APIs; it is not a file sandbox.
			recorded := &capture{header: make(http.Header), target: w}
			next.ServeHTTP(recorded, r)
			if recorded.passthrough {
				return
			}
			for key, values := range recorded.header {
				w.Header()[key] = values
			}
			w.Header().Set("Cache-Control", "private, no-store")
			w.Header().Add("Vary", "Authorization")
			w.Header().Add("Vary", "Cookie")
			w.Header().Del("Content-Length")
			w.Header().Del("ETag")
			if recorded.overflow {
				http.Error(w, `{"error":"response exceeds privacy inspection limit"}`, http.StatusRequestEntityTooLarge)
				return
			}
			body := recorded.body.Bytes()
			if strings.Contains(recorded.header.Get("Content-Type"), "json") && len(body) > 0 {
				var err error
				body, err = p.JSON(body)
				if err != nil {
					http.Error(w, `{"error":"privacy projection failed"}`, http.StatusInternalServerError)
					return
				}
			}
			status := recorded.status
			if status == 0 {
				status = http.StatusOK
			}
			w.WriteHeader(status)
			_, _ = w.Write(body)
		})
	}
}
