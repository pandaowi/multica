package handler

import (
	"bytes"
	"encoding/json"
	"github.com/multica-ai/multica/server/internal/middleware"
	"github.com/multica-ai/multica/server/internal/providerprivacy"
	"github.com/multica-ai/multica/server/internal/util"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
	"io"
	"net/http"
	"strings"
)

// ProviderPrivacy uses authenticated identity and database-owned resource fields.
func (h *Handler) ProviderPrivacy(next http.Handler) http.Handler {
	policy := providerprivacy.Default()
	filtered := policy.Middleware(func(r *http.Request) providerprivacy.Identity {
		return providerprivacy.Identity{UserID: r.Header.Get("X-User-ID"), WorkspaceID: middleware.ResolveWorkspaceIDFromRequest(r, h.Queries)}
	})(next)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		user := r.Header.Get("X-User-ID")
		if !policy.Enabled() || policy.IsOperator(user) {
			filtered.ServeHTTP(w, r)
			return
		}
		parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
		if len(parts) < 2 || parts[0] != "api" {
			filtered.ServeHTTP(w, r)
			return
		}
		deny := func() { writeError(w, http.StatusForbidden, "provider internal resource requires operator access") }
		if len(parts) > 2 && parts[1] == "attachments" {
			if id, err := util.ParseUUID(parts[2]); err == nil {
				if att, err := h.Queries.GetAttachmentByIDOnly(r.Context(), id); err == nil && policy.PrivateArtifact(uuidToString(att.WorkspaceID), uuidToString(att.ID), att.UploaderType, uuidToString(att.UploaderID)) {
					deny()
					return
				}
			}
		}
		workspace := middleware.ResolveWorkspaceIDFromRequest(r, h.Queries)
		ws, parseErr := util.ParseUUID(workspace)
		if parseErr != nil || !policy.Restricted(user, workspace) {
			filtered.ServeHTTP(w, r)
			return
		}
		write := r.Method != "GET" && r.Method != "HEAD" && r.Method != "OPTIONS"
		privateSkill := func(raw string) bool {
			id, err := util.ParseUUID(raw)
			if err != nil {
				return false
			}
			skill, err := h.Queries.GetSkillInWorkspace(r.Context(), db.GetSkillInWorkspaceParams{ID: id, WorkspaceID: ws})
			return err == nil && policy.PrivateSkillResource(workspace, uuidToString(skill.ID), uuidToString(skill.CreatedBy))
		}
		privateRuntime := func(raw string) bool {
			id, err := util.ParseUUID(raw)
			if err != nil {
				return false
			}
			runtime, err := h.Queries.GetAgentRuntimeForWorkspace(r.Context(), db.GetAgentRuntimeForWorkspaceParams{ID: id, WorkspaceID: ws})
			return err == nil && policy.ProviderOwned(workspace, uuidToString(runtime.OwnerID))
		}
		if len(parts) > 2 {
			if parts[1] == "skills" && privateSkill(parts[2]) {
				deny()
				return
			}
			if parts[1] == "runtimes" && (write || len(parts) > 3) && privateRuntime(parts[2]) {
				deny()
				return
			}
			if id, err := util.ParseUUID(parts[2]); err == nil {
				if parts[1] == "agents" && (write || len(parts) > 3 && (parts[3] == "env" || parts[3] == "mcp-servers" || parts[3] == "apps" || parts[3] == "runtime-skills")) {
					if a, err := h.Queries.GetAgentInWorkspace(r.Context(), db.GetAgentInWorkspaceParams{ID: id, WorkspaceID: ws}); err == nil && policy.ProviderOwned(workspace, uuidToString(a.OwnerID)) {
						deny()
						return
					}
				}
				if parts[1] == "squads" {
					if squad, err := h.Queries.GetSquadInWorkspace(r.Context(), db.GetSquadInWorkspaceParams{ID: id, WorkspaceID: ws}); err == nil && policy.ProviderOwned(workspace, uuidToString(squad.CreatorID)) {
						deny()
						return
					}
				}
			}
			if parts[1] == "agents" && len(parts) > 4 && parts[3] == "skills" && privateSkill(parts[4]) {
				deny()
				return
			}
		}
		if write && r.Body != nil && (parts[1] == "agents" || parts[1] == "agent-builder" || parts[1] == "skills" || parts[1] == "squads") {
			body, err := io.ReadAll(io.LimitReader(r.Body, (2<<20)+1))
			_ = r.Body.Close()
			if err != nil || len(body) > 2<<20 {
				writeError(w, http.StatusRequestEntityTooLarge, "request exceeds privacy inspection limit")
				return
			}
			r.Body = io.NopCloser(bytes.NewReader(body))
			var refs struct {
				RuntimeID string   `json:"runtime_id"`
				SkillIDs  []string `json:"skill_ids"`
			}
			if json.Unmarshal(body, &refs) == nil {
				if refs.RuntimeID != "" && privateRuntime(refs.RuntimeID) {
					deny()
					return
				}
				if len(refs.SkillIDs) > 128 {
					writeError(w, http.StatusRequestEntityTooLarge, "too many skill references")
					return
				}
				for _, id := range refs.SkillIDs {
					if privateSkill(id) {
						deny()
						return
					}
				}
			}
		}
		filtered.ServeHTTP(w, r)
	})
}
