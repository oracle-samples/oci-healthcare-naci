#!/bin/bash
# Update the system
yum update -y

# Install Java (Mirth Connect requires Java 17+)
yum install -y java-17-openjdk

# Download and install Mirth Connect
#wget -O mirthconnect.tar.gz "https://downloads.mirthcorp.com/connect/4.4.1.b326/mirthconnect-4.4.1.b326-unix.tar.gz"
wget  -O mirth-latest.rpm "https://s3.amazonaws.com/downloads.mirthcorp.com/connect/4.5.2.b363/mirthconnect-4.5.2.b363-linux.rpm"
yum install mirth-latest.rpm
# tar -xzf mirthconnect.tar.gz -C /opt/
#mv /opt/Mirth/ Connect /opt/mirthconnect

# Set up Mirth Connect as a service
cat <<EOT > /etc/systemd/system/mirthconnect.service
[Unit]
Description=Mirth Connect Integration Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/mirthconnect
ExecStart=/opt/mirthconnect/mirth-launcher.jar
Restart=always

[Install]
WantedBy=multi-user.target
EOT

# Enable and start the service
systemctl daemon-reload
systemctl enable mirthconnect
systemctl start mirthconnect

# Open firewall ports for Mirth Connect (if using firewalld)
firewall-cmd --permanent --add-port=8080/tcp
firewall-cmd --permanent --add-port=8443/tcp
firewall-cmd --reload