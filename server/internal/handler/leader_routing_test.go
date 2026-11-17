package handler

import (
	"context"
	"github.com/multica-ai/multica/server/internal/leaderrouting"
	"github.com/multica-ai/multica/server/internal/testutil"
	"net/http"
	"testing"
)

func TestLeaderRoutingScopedCommentPolicy(t *testing.T) {
	if testHandler == nil || testPool == nil {
		t.Fatal("isolated test database is required")
	}
	for _, tc := range []struct {
		name, body        string
		reviewer, mention bool
		want              int
	}{
		{"routine", "Progress: dependency install finished", false, false, 0},
		{"routine mentioned", "Progress: checking assets", false, true, 0},
		{"review ready", "Ready for review: integrated candidate and tests passed", false, false, 1},
		{"blocker", "Blocker: integration cannot connect", false, false, 1},
		{"important change", "Important change: API response contract changed", false, false, 1},
		{"reviewer finding", "Session revocation needs remediation", true, false, 1},
		{"critical mislabeled", "Progress: critical vulnerability found", true, false, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			fx := newSquadCommentTriggerFixture(t)
			issueID := uuidToString(fx.Issue.ID)
			h := *testHandler
			reviewers := []string{"unrelated-reviewer"}
			if tc.reviewer {
				reviewers = append(reviewers, fx.OtherID)
			}
			h.LeaderRoutingPolicy = leaderrouting.New(leaderrouting.Config{WorkspaceID: uuidToString(fx.Issue.WorkspaceID), SquadID: fx.SquadID, LeaderID: fx.LeaderID, ReviewerIDs: reviewers})
			taskID := dbfx.Task(t, fx.OtherID, testutil.Cols{"runtime_id": testRuntimeID, "issue_id": issueID, "status": "running", "originator_user_id": testUserID, "accountable_user_id": testUserID})
			dbfx.Cleanup(t, `DELETE FROM comment WHERE issue_id=$1`, issueID)
			dbfx.Cleanup(t, `DELETE FROM agent_task_queue WHERE issue_id=$1`, issueID)
			body := tc.body
			if tc.mention {
				body += " [@Lead](mention://agent/" + fx.LeaderID + ")"
			}
			// Preview and actual enqueue use the same resolver/filter.
			triggers, _ := h.computeCommentAgentTriggers(context.Background(), fx.Issue, body, nil, "agent", fx.OtherID, commentTriggerComputeOptions{OriginatorUserID: testUserID})
			if len(triggers) != tc.want {
				t.Fatalf("preview triggers=%d want=%d", len(triggers), tc.want)
			}
			r := newRequest(http.MethodPost, "/api/issues/"+issueID+"/comments", map[string]any{"content": body})
			r.Header.Set("X-Agent-ID", fx.OtherID)
			r.Header.Set("X-Task-ID", taskID)
			r = withURLParam(r, "id", issueID)
			testutil.Call(t, h.CreateComment, r).Want(http.StatusCreated)
			n := dbfx.Count(t, `SELECT count(*) FROM agent_task_queue WHERE issue_id=$1 AND agent_id=$2 AND is_leader_task=TRUE`, issueID, fx.LeaderID)
			if n != tc.want {
				t.Fatalf("leader tasks=%d want=%d", n, tc.want)
			}
		})
	}
}
