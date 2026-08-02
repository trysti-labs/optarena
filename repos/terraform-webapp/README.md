# terraform-webapp

A small multi-file Terraform module (AWS provider `~> 5.0`) for a
web-app's infrastructure: VPC/subnets, security groups, an RDS
Postgres instance, an ASG + launch template, an ALB, and a CloudWatch
CPU alarm - each concern in its own `.tf` file, all loaded together by
Terraform as one configuration.

Cases are checked with `python3 check_X.py` scripts that run the real
`terraform init -backend=false -input=false` + `terraform validate
-no-color -json` (the sandbox image mirrors the `hashicorp/aws` provider
offline, so this works fully under `--network none`), plus structural
assertions against the `.tf` source.
