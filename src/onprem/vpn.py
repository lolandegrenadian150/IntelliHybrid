"""
IntelliHybrid — VPN Manager
Manages AWS Site-to-Site VPN, OpenVPN, and AWS Direct Connect configurations.
"""

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional

import boto3
from botocore.exceptions import ClientError

from src.core.config_loader import HybridConfig, VPNConfig

logger = logging.getLogger(__name__)


class VPNManager:
    """
    Orchestrates VPN tunnel setup between on-premise and AWS VPC.
    Supports:
      - AWS Site-to-Site VPN (auto-provisions via AWS APIs)
      - OpenVPN (runs ovpn config via subprocess)
      - AWS Direct Connect (validates and describes existing connection)
    """

    def __init__(self, config: HybridConfig):
        self.config = config
        self.vpn_cfg: VPNConfig = config.onprem.vpn
        self.ec2 = boto3.client(
            "ec2",
            region_name=config.aws.region,
            aws_access_key_id=config.aws.access_key_id,
            aws_secret_access_key=config.aws.secret_access_key,
            aws_session_token=config.aws.session_token,
        )

    def establish(self) -> Dict:
        """
        Establish the VPN tunnel based on configured type.
        Returns a status dict.
        """
        vpn_type = self.vpn_cfg.type
        logger.info(f"Establishing VPN tunnel — type: {vpn_type}")

        if vpn_type == "site-to-site":
            return self._setup_site_to_site()
        elif vpn_type == "openvpn":
            return self._setup_openvpn()
        elif vpn_type == "direct-connect":
            return self._validate_direct_connect()
        else:
            raise ValueError(f"Unknown VPN type: {vpn_type}")

    # ------------------------------------------------------------------ #
    #  AWS Site-to-Site VPN
    # ------------------------------------------------------------------ #

    def _setup_site_to_site(self) -> Dict:
        """
        Provision AWS Site-to-Site VPN:
        1. Create or reuse Customer Gateway (CGW)
        2. Create or reuse Virtual Private Gateway (VGW) and attach to VPC
        3. Create VPN Connection
        4. Download & return tunnel configuration
        """
        cgw_id = self._get_or_create_customer_gateway()
        vgw_id = self._get_or_create_virtual_private_gateway()
        connection = self._get_or_create_vpn_connection(cgw_id, vgw_id)

        tunnel_configs = self._extract_tunnel_configs(connection)
        self._save_tunnel_config(tunnel_configs)

        return {
            "status": "established",
            "type": "site-to-site",
            "customer_gateway_id": cgw_id,
            "virtual_private_gateway_id": vgw_id,
            "vpn_connection_id": connection["VpnConnectionId"],
            "tunnel_1_outside_ip": tunnel_configs[0].get("OutsideIpAddress"),
            "tunnel_2_outside_ip": tunnel_configs[1].get("OutsideIpAddress") if len(tunnel_configs) > 1 else None,
            "tip": "Configure your on-prem firewall/router with the tunnel IPs above.",
        }

    def _get_or_create_customer_gateway(self) -> str:
        ip = self.vpn_cfg.customer_gateway_ip
        # Check if already exists
        response = self.ec2.describe_customer_gateways(
            Filters=[
                {"Name": "ip-address", "Values": [ip]},
                {"Name": "state", "Values": ["available"]},
            ]
        )
        existing = response.get("CustomerGateways", [])
        if existing:
            cgw_id = existing[0]["CustomerGatewayId"]
            logger.info(f"Reusing existing Customer Gateway: {cgw_id}")
            return cgw_id

        response = self.ec2.create_customer_gateway(
            Type="ipsec.1",
            PublicIp=ip,
            BgpAsn=self.vpn_cfg.bgp_asn,
            TagSpecifications=[{
                "ResourceType": "customer-gateway",
                "Tags": [
                    {"Key": "Name", "Value": "IntelliHybrid-CGW"},
                    {"Key": "ManagedBy", "Value": "IntelliHybrid"},
                ],
            }],
        )
        cgw_id = response["CustomerGateway"]["CustomerGatewayId"]
        logger.info(f"Created Customer Gateway: {cgw_id}")
        return cgw_id

    def _get_or_create_virtual_private_gateway(self) -> str:
        response = self.ec2.describe_vpn_gateways(
            Filters=[
                {"Name": "state", "Values": ["available"]},
                {"Name": "tag:ManagedBy", "Values": ["IntelliHybrid"]},
            ]
        )
        existing = response.get("VpnGateways", [])
        if existing:
            vgw_id = existing[0]["VpnGatewayId"]
            logger.info(f"Reusing existing Virtual Private Gateway: {vgw_id}")
            return vgw_id

        response = self.ec2.create_vpn_gateway(
            Type="ipsec.1",
            TagSpecifications=[{
                "ResourceType": "vpn-gateway",
                "Tags": [
                    {"Key": "Name", "Value": "IntelliHybrid-VGW"},
                    {"Key": "ManagedBy", "Value": "IntelliHybrid"},
                ],
            }],
        )
        vgw_id = response["VpnGateway"]["VpnGatewayId"]
        logger.info(f"Created Virtual Private Gateway: {vgw_id}")
        return vgw_id

    def _get_or_create_vpn_connection(self, cgw_id: str, vgw_id: str) -> Dict:
        response = self.ec2.describe_vpn_connections(
            Filters=[
                {"Name": "customer-gateway-id", "Values": [cgw_id]},
                {"Name": "vpn-gateway-id", "Values": [vgw_id]},
                {"Name": "state", "Values": ["available", "pending"]},
            ]
        )
        existing = response.get("VpnConnections", [])
        if existing:
            logger.info(f"Reusing existing VPN connection: {existing[0]['VpnConnectionId']}")
            return existing[0]

        options: Dict = {"StaticRoutesOnly": False}
        if self.vpn_cfg.tunnel_inside_cidr:
            options["TunnelOptions"] = [
                {"TunnelInsideCidr": self.vpn_cfg.tunnel_inside_cidr}
            ]

        response = self.ec2.create_vpn_connection(
            Type="ipsec.1",
            CustomerGatewayId=cgw_id,
            VpnGatewayId=vgw_id,
            Options=options,
            TagSpecifications=[{
                "ResourceType": "vpn-connection",
                "Tags": [
                    {"Key": "Name", "Value": "IntelliHybrid-VPN"},
                    {"Key": "ManagedBy", "Value": "IntelliHybrid"},
                ],
            }],
        )
        conn = response["VpnConnection"]
        logger.info(f"Created VPN Connection: {conn['VpnConnectionId']}")
        return conn

    def _extract_tunnel_configs(self, connection: Dict):
        """Parse tunnel endpoint IPs from the VPN connection response."""
        tunnels = connection.get("VgwTelemetry", [])
        return [
            {"OutsideIpAddress": t.get("OutsideIpAddress"), "Status": t.get("Status")}
            for t in tunnels
        ]

    def _save_tunnel_config(self, configs: list):
        path = Path("config/tunnel_config.json")
        path.parent.mkdir(exist_ok=True)
        with open(path, "w") as f:
            json.dump(configs, f, indent=2)
        logger.info(f"Tunnel config saved to {path}")

    # ------------------------------------------------------------------ #
    #  OpenVPN
    # ------------------------------------------------------------------ #

    def _setup_openvpn(self) -> Dict:
        """Launch OpenVPN using the provided .ovpn config file."""
        ovpn_file = self.vpn_cfg.config_file
        if not ovpn_file:
            raise ValueError(
                "VPN type 'openvpn' requires 'config_file' pointing to your .ovpn file."
            )
        if not Path(ovpn_file).exists():
            raise FileNotFoundError(f"OpenVPN config file not found: {ovpn_file}")

        cmd = ["openvpn", "--config", ovpn_file, "--daemon", "--log", "logs/openvpn.log"]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(f"OpenVPN failed to start: {result.stderr}")

        logger.info("OpenVPN daemon started. Check logs/openvpn.log for details.")
        time.sleep(3)  # give it a moment to establish

        return {
            "status": "established",
            "type": "openvpn",
            "config_file": ovpn_file,
            "log": "logs/openvpn.log",
        }

    # ------------------------------------------------------------------ #
    #  Direct Connect
    # ------------------------------------------------------------------ #

    def _validate_direct_connect(self) -> Dict:
        """Validate an existing AWS Direct Connect connection."""
        dx = boto3.client(
            "directconnect",
            region_name=self.config.aws.region,
            aws_access_key_id=self.config.aws.access_key_id,
            aws_secret_access_key=self.config.aws.secret_access_key,
        )
        response = dx.describe_connections()
        connections = response.get("connections", [])
        available = [c for c in connections if c.get("connectionState") == "available"]
        if not available:
            raise RuntimeError(
                "No available AWS Direct Connect connections found. "
                "Provision one in the AWS Console under Direct Connect first."
            )
        conn = available[0]
        logger.info(f"Direct Connect connection found: {conn['connectionId']} ({conn['connectionName']})")
        return {
            "status": "established",
            "type": "direct-connect",
            "connection_id": conn["connectionId"],
            "connection_name": conn["connectionName"],
            "bandwidth": conn.get("bandwidth"),
            "location": conn.get("location"),
        }

    def get_vpn_status(self) -> Dict:
        """Return current VPN tunnel status."""
        if self.vpn_cfg.type != "site-to-site":
            return {"status": "unknown — only site-to-site supports programmatic status checks"}
        try:
            config_path = Path("config/tunnel_config.json")
            if config_path.exists():
                with open(config_path) as f:
                    tunnels = json.load(f)
                return {"tunnels": tunnels}
        except Exception:
            pass
        return {"status": "no_tunnel_config_found"}
