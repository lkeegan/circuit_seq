# SampleFlow website deployment info

Some information on how to deploy the website - currently it is deployed on a heicloud VM.

## Production deployment

Production docker container images are automatically built by CI.
To deploy the latest version on a virtual machine with docker-compose installed,
download [docker-compose.yml](https://raw.githubusercontent.com/ssciwr/sample_flow/main/docker-compose.yml), then do

```
sudo docker-compose pull
sudo docker-compose up -d
```

The location of data directory, SSL keys and secret key should be set
either in env vars or in a file `.env` in the same location as the docker-compose.yml.

For example the current deployment on heicloud looks like this:

```
SAMPLE_FLOW_DATA="/home/ubuntu/sample_flow/docker_volume"
SAMPLE_FLOW_SSL_CERT="/etc/letsencrypt/live/circuitseq.iwr.uni-heidelberg.de/fullchain.pem"
SAMPLE_FLOW_SSL_KEY="/etc/letsencrypt/live/circuitseq.iwr.uni-heidelberg.de/privkey.pem"
SAMPLE_FLOW_JWT_SECRET_KEY="abc123" # to generate a new secret key: `python -c "import secrets; print(secrets.token_urlsafe(64))"`
```

The current status of the containers can be checked with

```
sudo docker-compose ps
sudo docker-compose logs
```

### Renew SSL certificate

To create or renew the SSL certificates using Certbot (this can be ran as a cron job):

```
sudo docker run -it --rm -v/etc/letsencrypt:/etc/letsencrypt -v/var/www/certbot:/var/www/certbot certbot/certbot certonly --webroot --webroot-path /var/www/certbot/ -n -d circuitseq.iwr.uni-heidelberg.de
```

### Give users admin rights

To make an existing user with email `user@embl.de` into an admin, ssh into the VM, then

```
cd sample_flow
sudo sqlite3 docker_volume/SampleFlow.db
sqlite> UPDATE user SET is_admin=true WHERE email='user@embl.de';
sqlite> .quit
```
