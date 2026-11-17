package leaderrouting

import "testing"

func TestCoordinatorWakePolicy(t *testing.T) {
	p := New(Config{WorkspaceID: "dev", SquadID: "squad", LeaderID: "lead", ReviewerIDs: []string{"qa", "security"}})
	for _, tc := range []struct {
		name, author, body       string
		explicit, terminal, want bool
	}{
		{"progress", "worker", "Progress: installing dependencies", false, false, false},
		{"progress explicit mention", "worker", "Progress: [@Lead](mention://agent/lead) compiling", true, false, false},
		{"progress after completion", "worker", "Progress: tests starting", false, true, false},
		{"plain acknowledgement", "worker", "Thanks for the update", false, false, false},
		{"ready", "worker", "Ready for review: preview and tests passed", false, false, true},
		{"markdown heading", "worker", "## **Ready for review:** implementation integrated", false, false, true},
		{"blocker", "worker", "Blocker: database unavailable", false, false, true},
		{"decision", "worker", "Decision required: API contract changed", false, false, true},
		{"important change", "worker", "Important change: migration required", false, false, true},
		{"qa report", "qa", "19 tests passed on candidate 2", false, false, true},
		{"security report", "security", "Session revocation needs remediation", false, false, true},
		{"reviewer routine progress", "qa", "Progress: opening browser", false, false, false},
		{"failure mislabeled progress", "worker", "Progress: migration failed", false, false, true},
		{"critical mislabeled progress", "security", "Progress: critical vulnerability found", false, false, true},
		{"direct attention", "worker", "Please decide between these two contracts", true, false, true},
		{"completed fallback", "worker", "Implementation complete", false, true, true},
		{"human request", "member", "Please continue", false, false, true},
		{"human progress", "member", "Progress: new requirements", false, false, true},
		{"indonesian ready", "worker", "Siap review: aplikasi terintegrasi", false, false, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := p.Allow("dev", "squad", "lead", "agent", tc.author, tc.body, tc.explicit, tc.terminal); tc.author != "member" && got != tc.want {
				t.Fatalf("allow=%v want=%v", got, tc.want)
			}
			if tc.author == "member" && !p.Allow("dev", "squad", "lead", "member", "human", tc.body, false, false) {
				t.Fatal("human request lost")
			}
		})
	}
	if !p.Allow("other", "squad", "lead", "agent", "worker", "Progress: testing", false, false) {
		t.Fatal("other workspace changed")
	}
	if !p.Allow("dev", "other", "worker", "agent", "worker", "Progress: testing", false, false) {
		t.Fatal("worker delegation changed")
	}
	if !New(Config{}).Allow("dev", "squad", "lead", "agent", "worker", "Progress: testing", false, false) {
		t.Fatal("disabled policy changed old behavior")
	}
}
