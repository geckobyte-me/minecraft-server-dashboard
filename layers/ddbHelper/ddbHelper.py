import boto3
import logging
import os
from datetime import datetime, timezone
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key
from decimal import Decimal, InvalidOperation

logger = logging.getLogger()
logger.setLevel(logging.INFO)

session = boto3.session.Session()
aws_region = session.region_name

class CoreTableDyn:
    """
    DynamoDB helper class for CoreTable operations using PK/SK pattern.
    Handles Users, Servers, Roles, and UserServer relationships in a single table.
    """

    def __init__(self, table_name=None):
        dynamodb = boto3.resource('dynamodb', region_name=aws_region)
        core_table = table_name or os.getenv('CORE_TABLE_NAME')
        if not core_table:
            raise ValueError("CORE_TABLE_NAME environment variable not set")
        
        self.table = dynamodb.Table(core_table)
        self.VALID_ROLES = {'admin', 'moderator', 'viewer', 'support'}

    # Server Operations
    def get_server_info(self, instance_id):
        """Get server information from CoreTable."""
        response = self.table.get_item(Key={'PK': f'SERVER#{instance_id}', 'SK': 'METADATA'})
        
        if 'Item' not in response:
            return None
        
        item = response['Item']
        return {
            'id': instance_id,
            'name': item.get('name'),
            'hostname': item.get('hostname'),
            'region': item.get('region'),
            'state': item.get('state'),
            'vCpus': self._safe_int(item.get('vCpus')),
            'memSize': self._safe_int(item.get('memSize')),
            'diskSize': self._safe_int(item.get('diskSize')),
            'launchTime': item.get('launchTime'),
            'publicIp': item.get('publicIp'),
            'initStatus': item.get('initStatus'),
            'iamStatus': item.get('iamStatus'),
            'runningMinutes': item.get('runningMinutes'),
            'runningMinutesCacheTimestamp': item.get('runningMinutesCacheTimestamp'),
            'configStatus': item.get('configStatus'),
            'configValid': item.get('configValid'),
            'configWarnings': item.get('configWarnings', []),
            'configErrors': item.get('configErrors', []),
            'autoConfigured': item.get('autoConfigured', False)
        }

    def put_server_info(self, server_info):
        """Save server information to CoreTable."""
        instance_id = server_info.get('id')
        if not instance_id:
            raise ValueError("Server info must include 'id' field")
        
        item = {
            'PK': f'SERVER#{instance_id}',
            'SK': 'METADATA',
            'Type': 'Server',
        }
        
        # Add fields, converting numbers to Decimal
        for k, v in server_info.items():
            if k == 'id' or v is None:
                continue
            if isinstance(v, (int, float)):
                item[k] = self._to_decimal(v)
            else:
                item[k] = v
        
        self.table.put_item(Item=item)
        return {'ResponseMetadata': {'HTTPStatusCode': 200}}

    def get_server_config(self, instance_id):
        """Get server configuration from CoreTable."""
        response = self.table.get_item(Key={'PK': f'SERVER#{instance_id}', 'SK': 'METADATA'})
        
        if 'Item' not in response:
            return None
        
        item = response['Item']
        return {
            'id': instance_id,
            'stopScheduleExpression': item.get('stopScheduleExpression', ''),
            'startScheduleExpression': item.get('startScheduleExpression', ''),
            'alarmMetric': item.get('alarmMetric', 'CPUUtilization'),
            'alarmThreshold': self._safe_float(item.get('alarmThreshold'), 0.0),
            'alarmEvaluationPeriod': self._safe_int(item.get('alarmEvaluationPeriod'), 0),
            'timezone': item.get('timezone', 'UTC'),
            'hasCognitoGroup': bool(item.get('hasCognitoGroup', False)),
            'createdAt': item.get('createdAt', ''),
            'updatedAt': item.get('updatedAt', '')
        }

    def put_server_config(self, config):
        """Save server configuration to CoreTable."""
        instance_id = config.get('id')
        if not instance_id:
            raise ValueError("Instance ID is required")
        
        now = datetime.now(timezone.utc).isoformat()
        existing = self.table.get_item(Key={'PK': f'SERVER#{instance_id}', 'SK': 'METADATA'}).get('Item', {})
        
        existing.update({
            'PK': f'SERVER#{instance_id}',
            'SK': 'METADATA',
            'Type': 'Server',
            'stopScheduleExpression': config.get('stopScheduleExpression', ''),
            'startScheduleExpression': config.get('startScheduleExpression', ''),
            'alarmMetric': config.get('alarmMetric', 'CPUUtilization'),
            'alarmThreshold': self._to_decimal(round(config.get('alarmThreshold', 0.0), 1)),
            'alarmEvaluationPeriod': config.get('alarmEvaluationPeriod', 0),
            'timezone': config.get('timezone', 'UTC'),
            'updatedAt': now,
            'createdAt': existing.get('createdAt', config.get('createdAt', now))
        })

        if 'hasCognitoGroup' in config:
            existing['hasCognitoGroup'] = config['hasCognitoGroup']

        self.table.put_item(Item=existing)
        return self.get_server_config(instance_id)

    def update_server_config(self, config):
        """Update server configuration."""
        return self.put_server_config(config)

    def update_server_name(self, instance_id, new_name):
        """Update server name."""
        return self.table.update_item(
            Key={'PK': f'SERVER#{instance_id}', 'SK': 'METADATA'},
            UpdateExpression="SET #name = :name",
            ExpressionAttributeNames={'#name': 'name'},
            ExpressionAttributeValues={':name': new_name},
            ReturnValues="ALL_NEW"
        )

    # User Operations
    def check_user_server_access(self, user_id, server_id):
        """Check if user has access to specific server."""
        response = self.table.get_item(Key={'PK': f'USER#{user_id}', 'SK': f'SERVER#{server_id}'})
        
        if 'Item' in response:
            item = response['Item']
            return {
                'userId': user_id,
                'serverId': server_id,
                'role': item.get('role'),
                'permissions': item.get('permissions', [])
            }
        return None

    def check_global_admin(self, user_id):
        """Check if user has global admin role."""
        response = self.table.get_item(Key={'PK': f'USER#{user_id}', 'SK': 'ADMIN'})
        return 'Item' in response

    def list_user_servers(self, user_id):
        """List all servers a user has access to."""
        response = self.table.query(
            KeyConditionExpression=Key('PK').eq(f'USER#{user_id}') & Key('SK').begins_with('SERVER#')
        )
        
        servers = []
        for item in response.get('Items', []):
            server_id = item['SK'].replace('SERVER#', '')
            servers.append({
                'serverId': server_id,
                'role': item.get('role'),
                'permissions': item.get('permissions', [])
            })
        
        return servers

    def list_server_members(self, server_id):
        """List all members of a server using GSI."""
        response = self.table.query(
            IndexName='SK-PK-index',
            KeyConditionExpression=Key('SK').eq(f'SERVER#{server_id}')
        )
        
        members = []
        for item in response.get('Items', []):
            if item['PK'].startswith('USER#'):
                user_id = item['PK'].replace('USER#', '')
                members.append({
                    'userId': user_id,
                    'serverId': server_id,
                    'role': item.get('role'),
                    'permissions': item.get('permissions', [])
                })
        
        return members

    def create_user_server_membership(self, user_id, server_id, role, permissions=None):
        """Create user-server membership."""
        if role not in self.VALID_ROLES:
            raise ValueError(f"Invalid role '{role}'. Must be one of: {', '.join(self.VALID_ROLES)}")
        
        item = {
            'PK': f'USER#{user_id}',
            'SK': f'SERVER#{server_id}',
            'Type': 'UserServer',
            'role': role,
            'permissions': permissions or []
        }
        
        try:
            self.table.put_item(
                Item=item,
                ConditionExpression='attribute_not_exists(PK) AND attribute_not_exists(SK)'
            )
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                raise ValueError("Membership already exists")
            raise

    def check_user_authorization(self, user_sub, server_id, required_permission='read_server'):
        """
        Check if user is authorized to perform actions on server using CoreTable.
        
        Args:
            user_sub (str): Cognito user sub
            server_id (str): EC2 instance ID
            required_permission (str): Permission level required
            
        Returns:
            tuple: (bool, str, str) - (is_authorized, user_role, auth_reason)
        """
        try:
            # Check if user has global admin role
            if self.check_global_admin(user_sub):
                return True, 'admin', "User has global admin privileges"
            
            # Check server-specific access
            server_access = self.check_user_server_access(user_sub, server_id)
            if server_access:
                role = server_access['role']
                
                # Simple permission check based on role
                permission_levels = {'viewer': 1, 'moderator': 2, 'support':3, 'admin': 4}
                required_levels = {
                    'read_server': 1, 'read_metrics': 1, 'read_config': 1,
                    'manage_server': 3, 'manage_users': 4
                }
                
                user_level = permission_levels.get(role, 0)
                required_level = required_levels.get(required_permission, 1)
                
                if user_level >= required_level:
                    return True, role, f"User has {role} role with sufficient permissions"
                else:
                    return False, role, f"Insufficient permissions. Required: {required_permission}, Role: {role}"
            
            return False, None, f"User does not have access to server {server_id}"
            
        except Exception as e:
            logger.error(f"Error checking user authorization: {str(e)}")
            return False, None, f"Error checking permissions: {str(e)}"

    # Utility methods
    @staticmethod
    def _to_decimal(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            # Handle special float values
            if isinstance(value, float) and (value != value or value == float('inf') or value == float('-inf')):
                return None
            try:
                return Decimal(str(value))
            except (ValueError, InvalidOperation):
                return None
        return value

    @staticmethod
    def _safe_float(value, default=0.0):
        if value is None:
            return None if default is None else default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _safe_int(value, default=0):
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

class LogAuditTable:
    """DynamoDB helper for LogAuditTable - tracks SSM commands and server actions."""
    
    def __init__(self, table_name=None):
        dynamodb = boto3.resource('dynamodb', region_name=aws_region)
        audit_table = table_name or os.getenv('LOG_AUDIT_TABLE_NAME')
        if not audit_table:
            raise ValueError("LOG_AUDIT_TABLE_NAME environment variable not set")
        self.table = dynamodb.Table(audit_table)
    
    def log_action(self, instance_id, action, action_user='system', command_id=None, metadata=None):
        """
        Log server action to audit table.
        
        Args:
            instance_id: EC2 instance ID
            action: Action type (start, stop, restart, bootstrap, etc.)
            action_user: User who initiated action (email or 'system')
            command_id: SSM command ID if applicable
            metadata: Additional context (dict)
        """
        timestamp = int(datetime.now(timezone.utc).timestamp())
        item = {
            'id': instance_id,
            'timestamp': timestamp,
            'action': action,
            'actionUser': action_user,
            'instanceId': instance_id,
            'createdAt': datetime.now(timezone.utc).isoformat()
        }
        
        if command_id:
            item['commandId'] = command_id
        if metadata:
            item['metadata'] = metadata
        
        self.table.put_item(Item=item)
        logger.info(f"Logged action: {action} for {instance_id} by {action_user}")

    def log_state_transition(self, instance_id, state, event_epoch, action_user='external'):
        """
        Record an EC2 state transition (running/stopped) for uptime history.

        These rows are namespaced with action 'state:<state>' so the uptime
        timeline can be queried independently of app click-audit rows. They are
        written from ec2StateHandler, which fires on EVERY EC2 state change
        (app, console, CLI, alarm, schedule), so the timeline is origin-agnostic.

        Args:
            instance_id: EC2 instance ID
            state: 'running' or 'stopped'
            event_epoch: Unix epoch (int seconds) of the transition
            action_user: Best-effort attribution ('external' for non-app origins)
        """
        timestamp = int(event_epoch)
        item = {
            'id': instance_id,
            'timestamp': timestamp,
            'action': f'state:{state}',
            'actionUser': action_user,
            'instanceId': instance_id,
            'createdAt': datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
        }
        self.table.put_item(Item=item)
        logger.info(f"Logged state transition: {state} for {instance_id} at {timestamp}")

    def query_state_history(self, instance_id, since_epoch=None, limit=200):
        """
        Return start/stop transition events for an instance, newest first.

        Args:
            instance_id: EC2 instance ID
            since_epoch: Optional Unix epoch lower bound (inclusive)
            limit: Max events to return

        Returns:
            list of {timestamp, state, actionUser} dicts (newest first)
        """
        key_cond = Key('id').eq(instance_id)
        if since_epoch is not None:
            key_cond = key_cond & Key('timestamp').gte(int(since_epoch))

        events = []
        kwargs = {
            'KeyConditionExpression': key_cond,
            'ScanIndexForward': False,  # newest first
            'Limit': limit
        }
        resp = self.table.query(**kwargs)
        for item in resp.get('Items', []):
            action = item.get('action', '')
            if not action.startswith('state:'):
                continue
            events.append({
                'timestamp': int(item['timestamp']),
                'state': action.split(':', 1)[1],
                'actionUser': item.get('actionUser', 'external')
            })
        return events

    @staticmethod
    def compute_running_seconds(events, now_epoch):
        """
        Compute total running seconds from a list of state events.

        Pairs each 'running' with the next 'stopped' (chronologically). If the
        instance is currently running (last event is 'running'), counts up to now.

        Args:
            events: list from query_state_history (any order; sorted internally)
            now_epoch: current Unix epoch for open-ended running periods

        Returns:
            int total running seconds
        """
        ordered = sorted(events, key=lambda e: e['timestamp'])
        total = 0
        running_since = None
        for e in ordered:
            if e['state'] == 'running' and running_since is None:
                running_since = e['timestamp']
            elif e['state'] == 'stopped' and running_since is not None:
                total += max(0, e['timestamp'] - running_since)
                running_since = None
        if running_since is not None:
            total += max(0, int(now_epoch) - running_since)
        return total

# Backward compatibility aliases
Dyn = CoreTableDyn
UserMembershipDyn = CoreTableDyn
