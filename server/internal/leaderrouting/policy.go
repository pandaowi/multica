// Package leaderrouting limits automatic coordinator wakeups without granting access.
package leaderrouting

import (
	"encoding/json"
	"os"
	"regexp"
	"strings"
	"sync"
)

type Config struct {
	WorkspaceID string   `json:"workspace_id"`
	SquadID     string   `json:"squad_id"`
	LeaderID    string   `json:"leader_id"`
	ReviewerIDs []string `json:"reviewer_ids"`
}
type Policy struct{ config Config }

func New(c Config) *Policy { return &Policy{config: c} }

var once sync.Once
var active *Policy

func Default() *Policy {
	once.Do(func() {
		path := os.Getenv("MULTICA_LEADER_ROUTING_CONFIG")
		if path == "" {
			active = New(Config{})
			return
		}
		body, err := os.ReadFile(path)
		var c Config
		if err != nil || json.Unmarshal(body, &c) != nil || c.WorkspaceID == "" || c.SquadID == "" || c.LeaderID == "" || len(c.ReviewerIDs) == 0 {
			panic("invalid leader routing configuration")
		}
		active = New(c)
	})
	return active
}
func (p *Policy) Applies(workspace, squad, leader string) bool {
	return p != nil && p.config.WorkspaceID != "" && workspace == p.config.WorkspaceID && (squad == p.config.SquadID || leader == p.config.LeaderID)
}
func (p *Policy) reviewer(id string) bool {
	for _, v := range p.config.ReviewerIDs {
		if v == id {
			return true
		}
	}
	return false
}

// Safety signals override a progress label. Conservative false positives are
// preferable to suppressing an incident reported with the wrong heading.
var safety = regexp.MustCompile(`(?i)\b(blocker|blocked|failed|failure|critical|security finding|qa fail|gagal|terhambat|temuan keamanan|kerentanan)\b`)

func heading(content string) string {
	content = strings.TrimSpace(content)
	if line, _, ok := strings.Cut(content, "\n"); ok {
		content = line
	}
	content = strings.TrimLeft(content, "# \t")
	return strings.ToLower(strings.ReplaceAll(content, "**", ""))
}
func hasLabel(line string, labels ...string) bool {
	for _, v := range labels {
		if strings.HasPrefix(line, v+":") {
			return true
		}
	}
	return false
}

// Allow is called only after normal routing/authorization resolved a target.
// Human control and uncertain reviewer/failure reports retain their old route.
func (p *Policy) Allow(workspace, squad, leader, authorType, authorID, content string, explicit, terminal bool) bool {
	if !p.Applies(workspace, squad, leader) || authorType != "agent" {
		return true
	}
	if safety.MatchString(content) {
		return true
	}
	line := heading(content)
	if hasLabel(line, "progress", "progres", "status", "update", "ack") {
		return false
	}
	if hasLabel(line, "blocker", "decision required", "important change", "ready for review", "security finding", "qa result", "keputusan diperlukan", "perubahan penting", "siap review", "hasil qa") {
		return true
	}
	if p.reviewer(authorID) || explicit || terminal {
		return true
	}
	return false
}
