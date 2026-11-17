package providerprivacy

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func fixture() *Policy {
	return New(Config{WorkspaceID: "development", OperatorUserIDs: []string{"provider"}, InternalSkillIDs: []string{"private-skill"}, ManagedAgentIDs: []string{"provider-agent"}, ManagedSquadIDs: []string{"provider-squad"}, ManagedRuntimeIDs: []string{"provider-runtime"}, PrivateStrings: []string{"10.0.0.10", "coder-development_ed25519"}})
}

func TestDenyPrivateResourcesAndMutations(t *testing.T) {
	p := fixture()
	for _, test := range []struct {
		method, path, body string
		deny               bool
	}{
		{"GET", "/api/skills/private-skill", "", true},
		{"GET", "/api/skills/private-skill/files", "", true},
		{"GET", "/api/skills/PRIVATE-SKILL/files", "", true},
		{"POST", "/api/skills/private-skill/refresh", "", true},
		{"PUT", "/api/agents/provider-agent", "{}", true},
		{"PUT", "/api/agents/provider-agent/skills", "{}", true},
		{"PUT", "/api/agents/provider-agent/runtime-skills/enabled", "{}", true},
		{"PUT", "/api/squads/provider-squad", "{}", true},
		{"GET", "/api/squads/provider-squad", "", true},
		{"GET", "/api/squads/provider-squad/members", "", true},
		{"GET", "/api/tasks/task/messages", "", false},
		{"GET", "/api/agents/provider-agent/env", "", true},
		{"GET", "/api/agents/provider-agent/mcp-servers", "", true},
		{"GET", "/api/tasks/task/context-receipt", "", true},
		{"GET", "/api/search-index/snapshot", "", true},
		{"POST", "/api/agents", `{"runtime_id":"provider-runtime"}`, true},
		{"POST", "/api/agents/customer-agent/skills/add", `{"skill_ids":["private-skill"]}`, true},
		{"POST", "/api/agents", `{"runtime_id":"customer-runtime","skill_ids":["private-skill"]}`, true},
		{"POST", "/api/skills/import", `{"runtime_id":"provider-runtime"}`, true},
		{"GET", "/api/runtimes/provider-runtime/skills", "", true},
		{"PUT", "/api/agents/customer-agent", `{"runtime_config":{"runtime_id":"provider-runtime"}}`, true},
		{"GET", "/api/agents/provider-agent", "", false},
		{"POST", "/api/chat/sessions", `{"agent_id":"provider-agent"}`, false},
		{"POST", "/api/chat/sessions/session/messages", `{"content":"Build my app"}`, false},
		{"GET", "/api/chat/sessions/session/messages", "", false},
		{"POST", "/api/tasks/task/cancel", "{}", false},
		{"PUT", "/api/agents/customer-agent", `{"instructions":"My application rules"}`, false},
		{"GET", "/api/skills/customer-skill/files", "", false},
	} {
		t.Run(test.method+test.path, func(t *testing.T) {
			if got := p.Deny("customer", "development", test.method, test.path, []byte(test.body)); got != test.deny {
				t.Fatalf("deny=%v, want %v", got, test.deny)
			}
			if p.Deny("provider", "development", test.method, test.path, []byte(test.body)) {
				t.Fatal("operator restricted")
			}
		})
	}
	if p.Restricted("customer", "other-workspace") {
		t.Fatal("unconfigured workspace restricted")
	}
	if !p.Restricted("customer", "") {
		t.Fatal("unknown scope must fail closed")
	}
}

func TestProjectionPreservesApplicationResultsAndOwnInstructions(t *testing.T) {
	p := fixture()
	input := `{"agents":[{"id":"provider-agent","name":"Frontend","instructions":"provider-only","system_instructions":"internal-system","runtime_config":{"key":"private"},"custom_args":["--private"],"skills":[{"id":"private-skill","name":"internal"}]},{"id":"customer-agent","instructions":"My product requirements","skills":[{"id":"customer-skill","name":"My coding guide"}]}],"chat":{"content":"Application ready at https://preview-app.preview.example.com/; file src/app.ts; session handling uses cookies"},"task":{"agent_id":"provider-agent","status":"completed","work_dir":"/home/provider/runtime","result":{"output":"shell output"},"error":"internal stack trace"}}`
	body, err := p.JSON([]byte(input))
	if err != nil {
		t.Fatal(err)
	}
	text := string(body)
	for _, secret := range []string{"provider-only", "internal-system", "--private", "private-skill", "/home/provider", "shell output", "internal stack trace"} {
		if strings.Contains(text, secret) {
			t.Fatalf("leaked %s", secret)
		}
	}
	for _, allowed := range []string{"My product requirements", "My coding guide", "https://preview-app.preview.example.com/", "src/app.ts"} {
		if !strings.Contains(text, allowed) {
			t.Fatalf("lost application data %s", allowed)
		}
	}
	var data map[string]any
	if json.Unmarshal(body, &data) != nil {
		t.Fatal("invalid JSON")
	}
	agents := data["agents"].([]any)
	agent := agents[0].(map[string]any)
	if agent["instructions"] != "" || agent["instructions_redacted"] != true {
		t.Fatal("missing instruction redaction")
	}
	if len(agent["skills"].([]any)) != 0 {
		t.Fatal("private skill binding not removed")
	}
}

func TestNewProviderOwnedResourcesArePrivateWithoutIDRegistration(t *testing.T) {
	p := fixture()
	raw := []byte(`{"agents":[{"id":"new-provider-agent","owner_id":"provider","instructions":"new private prompt"},{"id":"customer-agent","owner_id":"customer","instructions":"customer requirements"}],"skills":[{"id":"new-provider-skill","name":"New internal guide","created_by":"provider","config":{},"content":"new private guide"},{"id":"customer-guide","name":"Customer guide","created_by":"customer","config":{},"content":"customer code guide"}]}`)
	body, err := p.JSON(raw)
	if err != nil {
		t.Fatal(err)
	}
	for _, secret := range []string{"new private prompt", "new private guide", "new-provider-skill"} {
		if strings.Contains(string(body), secret) {
			t.Fatal("new provider resource leaked")
		}
	}
	for _, allowed := range []string{"customer requirements", "customer code guide"} {
		if !strings.Contains(string(body), allowed) {
			t.Fatal("customer-owned content removed")
		}
	}
	if !p.PrivateSkillResource("development", "new-provider-skill", "provider") {
		t.Fatal("new private skill not protected")
	}
	p.config.PublicSkillIDs = []string{"released-guide"}
	if p.PrivateSkillResource("development", "released-guide", "provider") {
		t.Fatal("explicit public skill release ignored")
	}
	frame := []byte(`{"type":"skill:updated","payload":{"skill":{"id":"new-provider-skill","name":"Guide","config":{},"created_by":"provider","content":"private"}}}`)
	if _, ok := p.Event("customer", "development", frame); ok {
		t.Fatal("new internal skill event leaked")
	}
}

func TestTextSanitization(t *testing.T) {
	p := fixture()
	body, err := p.JSON([]byte(`{"content":"Build succeeds. Source /home/coder/projects/app, server 10.0.0.10; key coder-development_ed25519. Edit src/app.ts."}`))
	if err != nil {
		t.Fatal(err)
	}
	for _, private := range []string{"/home/coder", "10.0.0.10", "coder-development_ed25519"} {
		if strings.Contains(string(body), private) {
			t.Fatal("private string remains")
		}
	}
	if !strings.Contains(string(body), "src/app.ts") {
		t.Fatal("relative app path removed")
	}
}

func TestEventsCoverReplayAndFinalMessages(t *testing.T) {
	p := fixture()
	for _, raw := range []string{
		`{"type":"task:message","payload":{"type":"tool_use","input":{"command":"ssh private"}}}`,
		`{"type":"task:message","payload":{"type":"thinking","content":"private reasoning"}}`,
		`{"type":"skill:updated","payload":{"skill":{"id":"private-skill","content":"private body"}}}`,
	} {
		if _, ok := p.Event("customer", "development", []byte(raw)); ok {
			t.Fatal("private live/replay event allowed")
		}
		if got, ok := p.Event("provider", "development", []byte(raw)); !ok || string(got) != raw {
			t.Fatal("operator event altered")
		}
	}
	for _, raw := range []string{
		`{"type":"task:running","payload":{"task_id":"task","status":"running"}}`,
		`{"type":"chat:message","payload":{"role":"assistant","content":"Application ready"}}`,
		`{"type":"skill:updated","payload":{"skill":{"id":"customer-skill","content":"My guide"}}}`,
	} {
		if _, ok := p.Event("customer", "development", []byte(raw)); !ok {
			t.Fatal("safe progress/result event blocked")
		}
	}
	frame := []byte(`{"type":"agent:updated","payload":{"agent":{"id":"provider-agent","instructions":"private","name":"Frontend"}}}`)
	clean, ok := p.Event("customer", "development", frame)
	if !ok || strings.Contains(string(clean), `"instructions":"private"`) {
		t.Fatal("broadcast instructions leak")
	}
	if _, ok := p.Event("customer", "development", []byte("invalid")); ok {
		t.Fatal("malformed frame failed open")
	}
}

func TestMiddlewareUsesAuthenticatedIdentityNotClaimedRole(t *testing.T) {
	p := fixture()
	resolve := func(*http.Request) Identity { return Identity{"customer", "development"} }
	h := p.Middleware(resolve)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Fatal("private handler reached") }))
	r := httptest.NewRequest("GET", "/api/skills/private-skill/files", nil)
	r.Header.Set("X-User-ID", "provider")
	r.Header.Set("X-Role", "owner")
	r.Header.Set("X-Actor-Source", "task_token")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != http.StatusForbidden {
		t.Fatalf("status=%d", w.Code)
	}
}

func TestMiddlewareProjectsListsAndDisablesSharedCaching(t *testing.T) {
	p := fixture()
	h := p.Middleware(func(*http.Request) Identity { return Identity{"customer", "development"} })(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("ETag", "operator-sensitive")
		_, _ = w.Write([]byte(`[{"id":"private-skill","content":"secret"},{"id":"customer-skill","content":"public"}]`))
	}))
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("GET", "/api/skills", nil))
	if w.Code != 200 || strings.Contains(w.Body.String(), "secret") {
		t.Fatal("list leak")
	}
	if w.Header().Get("ETag") != "" || w.Header().Get("Cache-Control") != "private, no-store" {
		t.Fatal("unsafe cache headers")
	}
	if !strings.Contains(strings.Join(w.Header().Values("Vary"), ","), "Authorization") {
		t.Fatal("missing auth vary")
	}
}

func TestMiddlewareFailsClosedOnMalformedJSON(t *testing.T) {
	h := fixture().Middleware(func(*http.Request) Identity { return Identity{"customer", "development"} })(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"instructions":"private`))
	}))
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("GET", "/api/agents", nil))
	if w.Code != 500 || strings.Contains(w.Body.String(), "instructions") {
		t.Fatal("malformed data exposed")
	}
}

func TestPrivateArtifactReleaseDoesNotDependOnCustomerRole(t *testing.T) {
	p := fixture()
	if !p.PrivateArtifact("development", "log", "agent", "provider-agent") {
		t.Fatal("agent log treated as public")
	}
	if !p.PrivateArtifact("development", "log", "member", "provider") {
		t.Fatal("operator upload treated as public")
	}
	if p.PrivateArtifact("development", "requirements", "member", "customer") {
		t.Fatal("customer upload blocked")
	}
	p.config.PublicAttachmentIDs = []string{"released-screenshot"}
	if p.PrivateArtifact("development", "released-screenshot", "agent", "provider-agent") {
		t.Fatal("approved artifact not released")
	}
	body, err := p.JSON([]byte(`{"attachments":[{"id":"log","workspace_id":"development","uploader_type":"agent","uploader_id":"provider-agent","download_url":"private capability"},{"id":"requirements","workspace_id":"development","uploader_type":"member","uploader_id":"customer","download_url":"customer upload"}]}`))
	if err != nil || strings.Contains(string(body), "private capability") || !strings.Contains(string(body), "customer upload") {
		t.Fatal("unsafe artifact projection")
	}
}

func TestNonJSONStreamingRemainsAvailable(t *testing.T) {
	h := fixture().Middleware(func(*http.Request) Identity { return Identity{"customer", "development"} })(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte("data: application status\n\n"))
		w.(http.Flusher).Flush()
	}))
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("GET", "/api/customer-stream", nil))
	if w.Body.String() != "data: application status\n\n" || !w.Flushed {
		t.Fatal("non-JSON stream broken")
	}
}

func TestProgressWhitelistForHistoryAndLive(t *testing.T) {
	p := fixture()
	raw := []byte(`[{"task_id":"task","seq":1,"type":"text","content":"Testing src/app.ts on 10.0.0.10 /home/coder/private","input":{"command":"secret command"},"output":"secret output","tool":"shell"},{"task_id":"task","seq":2,"type":"tool_use","input":{"command":"private shell"}},{"task_id":"task","seq":3,"type":"tool_result","output":"private result"},{"task_id":"task","seq":4,"type":"thinking","content":"private thought"},{"task_id":"task","seq":5,"type":"error","content":"private stack trace"}]`)
	clean, err := p.JSON(raw)
	if err != nil {
		t.Fatal(err)
	}
	var rows []map[string]any
	if json.Unmarshal(clean, &rows) != nil || len(rows) != 1 || rows[0]["type"] != "text" {
		t.Fatal("safe history unavailable")
	}
	for _, secret := range []string{"secret command", "secret output", "private shell", "private result", "private thought", "private stack trace", "10.0.0.10", "/home/coder", "\"input\"", "\"output\"", "\"tool\""} {
		if strings.Contains(string(clean), secret) {
			t.Fatalf("history leaked %s", secret)
		}
	}
	if !strings.Contains(string(clean), "src/app.ts") {
		t.Fatal("app progress lost")
	}
	frame := []byte(`{"type":"task:message","payload":{"task_id":"task","seq":1,"type":"text","content":"Running app checks /home/coder/project","input":{"command":"secret"},"output":"secret"}}`)
	event, ok := p.Event("customer", "development", frame)
	if !ok || !strings.Contains(string(event), "Running app checks") || strings.Contains(string(event), "secret") || strings.Contains(string(event), "/home/coder") {
		t.Fatal("live narrative projection failed")
	}
}

func TestSquadListShowsOnlyIdentity(t *testing.T) {
	p := fixture()
	body, err := p.JSON([]byte(`[{"id":"provider-squad","name":"Application Development","instructions":"private instructions","description":"private architecture","leader_id":"leader","creator_id":"provider","member_preview":[{"member_id":"worker"}],"member_count":5},{"id":"new-squad","name":"New Squad","instructions":"another secret","leader_id":"leader","creator_id":"provider"}]`))
	if err != nil {
		t.Fatal(err)
	}
	for _, s := range []string{"private instructions", "private architecture", "another secret", "worker", "\"leader\""} {
		if strings.Contains(string(body), s) {
			t.Fatal("squad detail leaked")
		}
	}
	var squads []map[string]any
	if json.Unmarshal(body, &squads) != nil || len(squads) != 2 || squads[0]["name"] != "Application Development" || squads[1]["name"] != "New Squad" || squads[0]["details_accessible"] != false {
		t.Fatal("squad identity lost")
	}
}
