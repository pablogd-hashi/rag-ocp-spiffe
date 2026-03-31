# OpenShift Local (CRC) Prerequisites

This guide covers the prerequisites and setup for running the RAG platform on OpenShift Local (formerly CodeReady Containers).

## System Requirements

### Minimum Requirements
- **Memory:** 16 GB RAM (20 GB recommended for Ollama with large models)
- **CPU:** 6 physical cores (8 recommended)
- **Disk Space:** 80 GB free space
  - CRC VM: ~35 GB
  - Ollama models: ~10 GB (mistral + nomic-embed-text)
  - Qdrant data: ~5 GB
  - Container images: ~15 GB
  - Build cache: ~10 GB
- **Operating System:**
  - macOS 11.0 (Big Sur) or later
  - Windows 10 Fall Creators Update (version 1709) or later
  - Linux: RHEL/CentOS 7.5+, Fedora 35+, Ubuntu 18.04+

### Network Requirements
- Internet connection for:
  - Downloading CRC bundle (~2.5 GB)
  - Pulling container images
  - Downloading Ollama models
- Ports available:
  - 6443: Kubernetes API server
  - 443: OpenShift web console and routes
  - 80: HTTP routes (optional)

## Software Prerequisites

### 1. OpenShift Local (CRC)

**Download:**
- Visit: https://developers.redhat.com/products/openshift-local/overview
- Requires Red Hat account (free)
- Download for your platform

**Installation:**

**macOS:**
```bash
# Extract the archive
tar xvf crc-macos-amd64.tar.xz

# Move to PATH
sudo mv crc-macos-*/crc /usr/local/bin/

# Verify installation
crc version
```

**Linux:**
```bash
# Extract the archive
tar xvf crc-linux-amd64.tar.xz

# Move to PATH
sudo mv crc-linux-*/crc /usr/local/bin/

# Verify installation
crc version
```

**Windows:**
```powershell
# Extract the ZIP file
# Add crc.exe to PATH
# Verify installation
crc version
```

### 2. OpenShift CLI (oc)

The `oc` CLI is bundled with CRC and automatically added to PATH after `crc setup`.

**Verify:**
```bash
oc version
```

**Manual Installation (if needed):**
- Download from: https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/
- Extract and add to PATH

### 3. Kubernetes CLI (kubectl)

**macOS (Homebrew):**
```bash
brew install kubectl
```

**Linux:**
```bash
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
```

**Windows (Chocolatey):**
```powershell
choco install kubernetes-cli
```

**Verify:**
```bash
kubectl version --client
```

### 4. Helm

**macOS (Homebrew):**
```bash
brew install helm
```

**Linux:**
```bash
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

**Windows (Chocolatey):**
```powershell
choco install kubernetes-helm
```

**Verify:**
```bash
helm version
```

### 5. HashiCorp Vault CLI

**macOS (Homebrew):**
```bash
brew tap hashicorp/tap
brew install hashicorp/tap/vault
```

**Linux:**
```bash
wget -O- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install vault
```

**Windows (Chocolatey):**
```powershell
choco install vault
```

**Verify:**
```bash
vault version
```

### 6. Git

**macOS (Homebrew):**
```bash
brew install git
```

**Linux:**
```bash
sudo apt install git  # Debian/Ubuntu
sudo yum install git  # RHEL/CentOS
```

**Windows:**
- Download from: https://git-scm.com/download/win

**Verify:**
```bash
git --version
```

### 7. jq (JSON processor)

**macOS (Homebrew):**
```bash
brew install jq
```

**Linux:**
```bash
sudo apt install jq  # Debian/Ubuntu
sudo yum install jq  # RHEL/CentOS
```

**Windows (Chocolatey):**
```powershell
choco install jq
```

**Verify:**
```bash
jq --version
```

## CRC Setup

### 1. Initial Setup

```bash
# Run setup (downloads VM bundle and configures hypervisor)
crc setup

# This will:
# - Download the OpenShift bundle (~2.5 GB)
# - Set up the hypervisor (HyperKit on macOS, KVM on Linux, Hyper-V on Windows)
# - Configure networking
# - Set up DNS resolution
```

### 2. Configure Resources

```bash
# Set memory (20 GB for Ollama)
crc config set memory 20480

# Set CPUs
crc config set cpus 6

# Set disk size (if needed, default is usually sufficient)
crc config set disk-size 100

# Enable monitoring (optional, for Prometheus/Grafana)
crc config set enable-cluster-monitoring true

# View all configuration
crc config view
```

### 3. Start CRC

```bash
# Start the cluster (first start takes 5-10 minutes)
crc start

# You'll be prompted for your pull secret
# Get it from: https://console.redhat.com/openshift/create/local
```

**Expected Output:**
```
INFO Checking if running as non-root
INFO Checking if crc-admin-helper executable is cached
INFO Checking for obsolete admin-helper executable
INFO Checking if running on a supported CPU architecture
INFO Checking minimum RAM requirements
INFO Checking if crc executable symlink exists
INFO Checking if running emulated on a M1 CPU
INFO Checking if vfkit is installed
INFO Checking if old launchd config for tray and/or daemon exists
INFO Checking if crc daemon plist file is present and loaded
INFO Loading bundle: crc_vfkit_4.14.7_amd64...
INFO Starting CRC VM for openshift 4.14.7...
INFO CRC instance is running with IP 192.168.127.2
INFO CRC VM is running
INFO Updating authorized keys...
INFO Configuring shared directories
INFO Check internal and public DNS query...
INFO Check DNS query from host...
INFO Verifying validity of the kubelet certificates...
INFO Starting kubelet service
INFO Waiting for kube-apiserver availability... [takes around 2min]
INFO Waiting for user's pull secret part of instance disk...
INFO Starting openshift instance... [takes around 2min]
INFO Operator authentication: Progressing (Working towards 4.14.7)
INFO Operator console: Progressing (Working towards 4.14.7)
INFO All operators are available. Ensuring stability...
INFO Operators are stable (2/3)...
INFO Operators are stable (3/3)...
INFO Adding crc-admin and crc-developer contexts to kubeconfig...
Started the OpenShift cluster.

The server is accessible via web console at:
  https://console-openshift-console.apps-crc.testing

Log in as administrator:
  Username: kubeadmin
  Password: <password-shown-here>

Log in as user:
  Username: developer
  Password: developer

Use the 'oc' command line interface:
  $ eval $(crc oc-env)
  $ oc login -u developer https://api.crc.testing:6443
```

### 4. Configure Shell Environment

```bash
# Add oc to PATH
eval $(crc oc-env)

# Add to your shell profile for persistence
echo 'eval $(crc oc-env)' >> ~/.bashrc  # or ~/.zshrc
```

### 5. Login to OpenShift

```bash
# Login as developer (default user)
oc login -u developer -p developer https://api.crc.testing:6443

# Or login as admin (for cluster-wide operations)
oc login -u kubeadmin -p <password-from-crc-start> https://api.crc.testing:6443
```

### 6. Verify Cluster

```bash
# Check cluster status
crc status

# Check nodes
oc get nodes

# Check cluster operators
oc get co

# Access web console
crc console
```

## Post-Setup Configuration

### 1. Create Project Namespace

```bash
# Login as developer
oc login -u developer -p developer

# Create project
oc new-project rag-platform

# Verify
oc project
```

### 2. Configure Image Registry (Optional)

```bash
# Enable default image registry (if not already enabled)
oc patch configs.imageregistry.operator.openshift.io cluster --type merge --patch '{"spec":{"managementState":"Managed"}}'

# Set storage to emptyDir for CRC (not for production!)
oc patch configs.imageregistry.operator.openshift.io cluster --type merge --patch '{"spec":{"storage":{"emptyDir":{}}}}'
```

### 3. Install Helm Repositories

```bash
# Add HashiCorp Helm repo (for Consul)
helm repo add hashicorp https://helm.releases.hashicorp.com

# Update repos
helm repo update

# Verify
helm search repo hashicorp/vault
helm search repo hashicorp/consul
```

## Troubleshooting

### CRC Won't Start

**Issue:** `crc start` fails with memory error
```bash
# Check current memory allocation
crc config get memory

# Increase memory
crc config set memory 20480

# Delete and recreate cluster
crc delete
crc start
```

**Issue:** `crc start` fails with CPU error
```bash
# Check current CPU allocation
crc config get cpus

# Increase CPUs
crc config set cpus 6

# Delete and recreate cluster
crc delete
crc start
```

### DNS Resolution Issues

**Issue:** Cannot resolve `*.apps-crc.testing` domains

**macOS:**
```bash
# Check DNS configuration
scutil --dns | grep apps-crc.testing

# Restart CRC
crc stop
crc start
```

**Linux:**
```bash
# Check NetworkManager configuration
cat /etc/NetworkManager/conf.d/crc-nm-dnsmasq.conf

# Restart NetworkManager
sudo systemctl restart NetworkManager

# Restart CRC
crc stop
crc start
```

**Windows:**
```powershell
# Check DNS configuration
ipconfig /all

# Restart CRC
crc stop
crc start
```

### Disk Space Issues

**Issue:** Running out of disk space

```bash
# Check CRC disk usage
crc status

# Clean up unused images
oc adm prune images --confirm

# Clean up completed pods
oc delete pods --field-selector=status.phase==Succeeded --all-namespaces

# Clean up failed pods
oc delete pods --field-selector=status.phase==Failed --all-namespaces
```

### Performance Issues

**Issue:** Cluster is slow or unresponsive

```bash
# Check resource usage
crc status

# Increase resources
crc stop
crc config set memory 24576
crc config set cpus 8
crc start

# Disable cluster monitoring if not needed
crc config set enable-cluster-monitoring false
crc delete
crc start
```

## Verification Checklist

Before proceeding with RAG platform deployment, verify:

- [ ] CRC is running: `crc status`
- [ ] Can login with oc: `oc login -u developer -p developer`
- [ ] Project created: `oc project rag-platform`
- [ ] Can access web console: `crc console`
- [ ] Helm is configured: `helm version`
- [ ] Vault CLI is available: `vault version`
- [ ] kubectl works: `kubectl get nodes`
- [ ] DNS resolution works: `ping console-openshift-console.apps-crc.testing`
- [ ] Sufficient resources: 20GB RAM, 6 CPUs, 80GB disk

## Next Steps

Once CRC is running and verified:

1. [Deploy Vault + Consul (Vault PKI as Connect CA)](vault-pki-consul-ca.md)
2. [Deploy RAG Platform](../architecture/openshift-deployment.md)
3. [Consul Connect + SPIFFE architecture](../architecture/consul-connect-spiffe.md)

## Useful Commands

```bash
# Start CRC
crc start

# Stop CRC
crc stop

# Delete CRC (removes all data)
crc delete

# Check status
crc status

# View configuration
crc config view

# Access web console
crc console

# Get cluster credentials
crc console --credentials

# SSH into CRC VM
crc ssh

# View logs
crc logs
```

## Resources

- OpenShift Local Documentation: https://access.redhat.com/documentation/en-us/red_hat_openshift_local
- OpenShift CLI Reference: https://docs.openshift.com/container-platform/latest/cli_reference/openshift_cli/getting-started-cli.html
- Kubernetes Documentation: https://kubernetes.io/docs/home/
- Helm Documentation: https://helm.sh/docs/
