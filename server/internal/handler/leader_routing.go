package handler

import (
	"context"
	"github.com/multica-ai/multica/server/internal/leaderrouting"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

// The existing resolver remains the authorization boundary. This filter can
// remove a wakeup, never create one or expand the resolved execution authority.
func (h *Handler) computeCommentAgentTriggers(ctx context.Context, issue db.Issue, content string, parent *db.Comment, actorType, actorID string, opts commentTriggerComputeOptions) ([]commentAgentTrigger, []commentMentionTarget) {
	triggers, targets := h.computeCommentAgentTriggersUnfiltered(ctx, issue, content, parent, actorType, actorID, opts)
	if len(triggers) == 0 {
		return triggers, targets
	}
	policy := h.LeaderRoutingPolicy
	if policy == nil {
		policy = leaderrouting.Default()
	}
	kept := make([]commentAgentTrigger, 0, len(triggers))
	suppressed := map[string]bool{}
	terminal, checked := false, false
	for _, trigger := range triggers {
		squad := ""
		if trigger.Squad != nil {
			squad = uuidToString(trigger.Squad.ID)
		}
		leader := uuidToString(trigger.Agent.ID)
		if !policy.Applies(uuidToString(issue.WorkspaceID), squad, leader) || actorType != "agent" {
			kept = append(kept, trigger)
			continue
		}
		if !checked && opts.AuthoringTaskID.Valid {
			checked = true
			if task, err := h.Queries.GetAgentTask(ctx, opts.AuthoringTaskID); err == nil && task.IssueID == issue.ID && uuidToString(task.AgentID) == actorID && !task.IsLeaderTask {
				terminal = task.Status == "completed" || task.Status == "failed" || task.Status == "cancelled"
			}
		}
		explicit := trigger.Source == commentTriggerSourceMentionAgent || trigger.Source == commentTriggerSourceMentionSquadLeader
		if policy.Allow(uuidToString(issue.WorkspaceID), squad, leader, actorType, actorID, content, explicit, terminal) {
			kept = append(kept, trigger)
		} else {
			suppressed[leader] = true
		}
	}
	for i := range targets {
		if suppressed[targets[i].ExecAgentID] {
			targets[i].ExecAgentID = ""
			targets[i].Status = DispatchBlocked
			targets[i].ReasonCode = DispatchReasonCode("routine_update")
		}
	}
	return kept, targets
}
