# Base is pinned to the currently approved rootless workspace image.
FROM sha256:9b3800ef1302eaba7a936da1a24b0faa848389dc98315e5319e62fc68ffd7a71
USER root
COPY --chown=1000:1000 development-tools /tmp/coder-kit-build
USER coder
RUN bash /tmp/coder-kit-build/install.sh
USER root
RUN mkdir -p /opt/coder-workspace-kit \
 && mv /home/coder/.local/share/coder-development-tools /opt/coder-workspace-kit/tools \
 && mv /home/coder/.cache/ms-playwright /opt/coder-workspace-kit/browsers \
 && rm -rf /tmp/coder-kit-build /opt/coder-workspace-kit/tools/apt \
 && printf '%s\n' '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig><dir>/opt/coder-workspace-kit/tools/browser-root/usr/share/fonts/truetype/dejavu</dir><cachedir>/home/coder/.cache/coder-platform-fonts</cachedir></fontconfig>' > /opt/coder-workspace-kit/tools/fonts.conf
COPY workspace-template/bootstrap.sh workspace-template/RELEASE /opt/coder-workspace-kit/
COPY development-tools/coder-dev development-tools/workspace-instructions.md /opt/coder-workspace-kit/
COPY app-platform/coder-preview app-platform/coder-preview-link app-platform/coder-publish /opt/coder-workspace-kit/
COPY workspace-template/AGENTS.md /opt/coder-workspace-kit/AGENTS.md
RUN chmod 755 /opt/coder-workspace-kit/bootstrap.sh /opt/coder-workspace-kit/coder-dev /opt/coder-workspace-kit/coder-preview /opt/coder-workspace-kit/coder-preview-link /opt/coder-workspace-kit/coder-publish \
 && chmod -R a+rX /opt/coder-workspace-kit
USER coder
