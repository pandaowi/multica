terraform {
  required_providers {
    coder = {
      source  = "coder/coder"
      version = "2.20.0"
    }
    docker = {
      source  = "kreuzwerker/docker"
      version = "3.9.0"
    }
  }
}

provider "coder" {
  # Agent traffic stays within the dedicated Docker network; users use HTTPS.
  url = "http://coder:7080"
}
provider "docker" {
  host = "unix:///var/run/docker.sock"
}

data "coder_provisioner" "me" {}
data "coder_workspace" "me" {}
data "coder_workspace_owner" "me" {}
data "docker_image" "workspace" {
  name = "__WORKSPACE_IMAGE__"
}

resource "coder_agent" "main" {
  arch = "amd64"
  os   = "linux"
  startup_script = <<-EOT
    set -eu
    if [ ! -f "$HOME/.init_done" ]; then
      cp -rT /etc/skel "$HOME"
      touch "$HOME/.init_done"
    fi
    mkdir -p "$HOME/projects" "$HOME/.local/bin"
    npm config set prefix "$HOME/.local"
    /opt/coder-workspace-kit/bootstrap.sh "${data.coder_workspace.me.id}"

  EOT
  env = {
    GIT_AUTHOR_NAME     = data.coder_workspace_owner.me.name
    GIT_AUTHOR_EMAIL    = data.coder_workspace_owner.me.email
    GIT_COMMITTER_NAME  = data.coder_workspace_owner.me.name
    GIT_COMMITTER_EMAIL = data.coder_workspace_owner.me.email
    PLAYWRIGHT_BROWSERS_PATH = "/opt/coder-workspace-kit/browsers"
    PATH               = "/home/coder/.local/bin:/usr/local/bin:/usr/bin:/bin"
  }
  metadata {
    display_name = "CPU"
    key          = "cpu"
    script       = "coder stat cpu"
    interval     = 10
    timeout      = 3
  }
  metadata {
    display_name = "RAM"
    key          = "ram"
    script       = "coder stat mem"
    interval     = 10
    timeout      = 3
  }
}

resource "coder_app" "preview" {
  agent_id     = coder_agent.main.id
  slug         = "preview"
  display_name = "Preview"
  url          = "http://localhost:3000"
  subdomain    = false
  share        = "owner"
  open_in      = "tab"
  order        = 1
  healthcheck {
    url       = "http://localhost:3000/"
    interval  = 5
    threshold = 6
  }
}

module "code-server" {
  source   = "registry.coder.com/coder/code-server/coder"
  version  = "1.6.0"
  count    = data.coder_workspace.me.start_count
  agent_id = coder_agent.main.id
  folder   = "/home/coder/projects"
}

resource "docker_volume" "home" {
  name = "coder-${data.coder_workspace.me.id}-home"
  lifecycle {
    ignore_changes = all
  }
  labels {
    label = "coder.workspace_id"
    value = data.coder_workspace.me.id
  }
  labels {
    label = "coder.owner"
    value = data.coder_workspace_owner.me.name
  }
}

resource "docker_container" "workspace" {
  count       = data.coder_workspace.me.start_count
  name        = "coder-ws-${data.coder_workspace.me.id}"
  hostname    = data.coder_workspace.me.name
  image       = data.docker_image.workspace.id
  user        = "1000:1000"
  start       = false
  must_run    = false
  entrypoint  = ["sh", "-c", coder_agent.main.init_script]
  env         = ["CODER_AGENT_TOKEN=${coder_agent.main.token}"]
  cpu_period  = 100000
  cpu_quota   = 200000
  memory      = 4096
  memory_swap = 4096
  privileged  = false
  security_opts = ["no-new-privileges:true"]
  capabilities {
    drop = ["ALL"]
  }
  networks_advanced {
    name = "coder_workspace"
  }
  volumes {
    volume_name    = docker_volume.home.name
    container_path = "/home/coder"
  }
  labels {
    label = "coder.workspace_id"
    value = data.coder_workspace.me.id
  }
  labels {
    label = "coder.owner"
    value = data.coder_workspace_owner.me.name
  }
}

# Docker provider 3.9 does not expose PidsLimit. Configure it through the
# dedicated rootless daemon API before starting the container.
resource "terraform_data" "start_workspace" {
  count            = data.coder_workspace.me.start_count
  triggers_replace = [docker_container.workspace[count.index].id]
  provisioner "local-exec" {
    command = <<-EOT
      set -eu
      curl --fail --silent --show-error --unix-socket /var/run/docker.sock \
        -H 'Content-Type: application/json' -X POST -d '{"PidsLimit":512}' \
        http://localhost/containers/${docker_container.workspace[count.index].id}/update
      curl --fail --silent --show-error --unix-socket /var/run/docker.sock \
        -X POST http://localhost/containers/${docker_container.workspace[count.index].id}/start
    EOT
  }
}
