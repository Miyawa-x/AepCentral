# Restricted CPU sandbox for Elite Power demo: Python + sshd.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server \
    && rm -rf /var/lib/apt/lists/* && mkdir -p /run/sshd \
    && sed -i 's/#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && pip install --no-cache-dir numpy==2.1.3
EXPOSE 22
CMD ["sh", "-c", "echo \"root:${ROOT_PASSWORD}\" | chpasswd && exec /usr/sbin/sshd -D -e"]
