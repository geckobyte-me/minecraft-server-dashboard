import boto3
import logging
import os
import json
import time
import re
import authHelper
import ec2Helper
import utilHelper
import ddbHelper
from aws_croniter import AwsCroniter
from botocore.session import Session
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.httpsession import URLLib3Session
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ec2_client = boto3.client('ec2')
scheduler_client = boto3.client('scheduler')

appValue = os.getenv('TAG_APP_VALUE')
appName = os.getenv('APP_NAME')
envName = os.getenv('ENVIRONMENT_NAME')
ec2_instance_profile_name = os.getenv('EC2_INSTANCE_PROFILE_NAME')
ec2_instance_profile_arn = os.getenv('EC2_INSTANCE_PROFILE_ARN')
cognito_pool_id = os.getenv('COGNITO_USER_POOL_ID')
endpoint = os.getenv('APPSYNC_URL', None)
scheduler_role_arn = os.getenv('SCHEDULER_ROLE_ARN')
scheduler_group_name = os.getenv('SCHEDULER_GROUP_NAME', 'default')

auth = authHelper.Auth(cognito_pool_id)
ec2_utils = ec2Helper.Ec2Utils()
utl = utilHelper.Utils()
core_dyn = ddbHelper.CoreTableDyn()
audit_table = ddbHelper.LogAuditTable()

_boto3_session = boto3.Session()
aws_region = _boto3_session.region_name
account_id = boto3.client('sts').get_caller_identity()['Account']


def send_status_to_appsync(action, instance_id, status, message=None, user_email=None):
    """
    Send action status to AppSync via the putec2ActionValidatorStatus mutation
    so the frontend receives it over the onPutec2ActionValidatorStatus subscription.

    Args:
        action: Action being performed
        instance_id: EC2 instance ID
        status: Status of the action (PROCESSING, COMPLETED, FAILED)
        message: Optional status message
        user_email: Email of user who initiated the action

    Returns:
        bool: True if the status was accepted by AppSync
    """
    logger.info(f"Action status: action={action}, instance={instance_id}, status={status}, user={user_email}, message={message}")

    if not endpoint:
        logger.warning("AppSync endpoint not configured; skipping status broadcast")
        return False

    mutation = """
    mutation Putec2ActionValidatorStatus($input: ec2ActionValidatorStatusInput!) {
        putec2ActionValidatorStatus(input: $input) {
            id
            action
            status
            message
            timestamp
        }
    }
    """
    variables = {
        "input": {
            "id": instance_id,
            "action": action,
            "status": status,
            "message": message or "",
            "userEmail": user_email or "",
            "timestamp": int(time.time())
        }
    }
    payload = {"query": mutation, "variables": variables}

    try:
        credentials = Session().get_credentials()
        request = AWSRequest(
            method='POST',
            url=endpoint,
            data=json.dumps(payload),
            headers={'Content-Type': 'application/json'}
        )
        SigV4Auth(credentials, 'appsync', aws_region).add_auth(request)
        response = URLLib3Session().send(request.prepare())
        if response.status_code == 200:
            return True
        logger.error(f"AppSync status request failed: {response.status_code} - {response.content}")
        return False
    except Exception as e:
        logger.error(f"Failed to send status to AppSync: {str(e)}")
        return False
            
def _is_valid_cron(cron_expression):
    """Basic validation for 5-field cron expressions."""
    if not cron_expression or not isinstance(cron_expression, str):
        return False
    parts = cron_expression.strip().split()
    if len(parts) != 5:
        return False
    cron_pattern = r'^[0-9*,/-]+$'
    return all(re.match(cron_pattern, part) for part in parts)


# ============================================================================
# Synchronous action execution (start/stop)
# ============================================================================

def _execute_ec2_action(action, instance_id):
    """Execute an EC2 start/stop call."""
    actions = {
        'start': lambda: ec2_client.start_instances(InstanceIds=[instance_id]),
        'stop': lambda: ec2_client.stop_instances(InstanceIds=[instance_id]),
    }
    try:
        actions[action]()
        return True
    except Exception as e:
        logger.error(f"EC2 {action} failed: {str(e)}")
        return False


def handle_server_action(action, instance_id, action_user='system'):
    """Start or stop an EC2 instance, with state validation and audit logging."""
    logger.info(f"Handling {action} for {instance_id}")

    server_info = core_dyn.get_server_info(instance_id)
    instance = ec2_utils.list_server_by_id(instance_id)
    if not instance or not instance.get('Instances'):
        logger.error(f"Instance not found: {instance_id}")
        return False

    state = instance['Instances'][0]["State"]["Name"]
    server_name = (server_info or {}).get('name', instance_id)

    valid_states = {'start': 'stopped', 'stop': 'running'}
    if state != valid_states.get(action):
        logger.warning(f"Cannot {action} instance in state {state}")
        return False

    if not _execute_ec2_action(action, instance_id):
        return False

    try:
        audit_table.log_action(
            instance_id=instance_id,
            action=action,
            action_user=action_user,
            metadata={'serverName': server_name, 'previousState': state}
        )
    except Exception as e:
        logger.warning(f"Audit log failed for {action} on {instance_id}: {str(e)}")

    logger.info(f"{action.capitalize()} initiated for {instance_id}")
    return True


# ============================================================================
# Auto-shutdown alarm configuration (CPU or Network)
# ============================================================================

def _configure_alarm_shutdown(instance_id, arguments):
    """Create/update or remove the CloudWatch auto-shutdown alarm."""
    alarm_threshold = arguments.get('alarmThreshold', 0)
    alarm_period = arguments.get('alarmEvaluationPeriod', 10) or 10
    # alarmMetric: "CPUUtilization" (default) or "NetworkOut"
    alarm_metric = arguments.get('alarmMetric') or 'CPUUtilization'

    if alarm_threshold and alarm_threshold > 0:
        ec2_utils.update_alarm(instance_id, alarm_metric, alarm_threshold, alarm_period)
    else:
        ec2_utils.remove_alarm(instance_id)


# ============================================================================
# Scheduled start/stop (EventBridge Scheduler) configuration
# ============================================================================

def _format_schedule_expression(cron_expression):
    """Normalize a 5-field standard cron into an EventBridge Scheduler cron() expression.

    Timezone handling is delegated to EventBridge Scheduler via the
    ScheduleExpressionTimezone parameter, so no manual UTC conversion is done
    here (this also keeps the schedule correct across DST transitions).

    Accepts either an already-formed ``cron(...)`` expression or a 5-field
    standard cron string (minute hour day-of-month month day-of-week).
    Returns a ``cron(...)`` string, or ``None`` if invalid.
    """
    if not cron_expression:
        return None
    cron_expression = cron_expression.strip()

    if cron_expression.startswith('cron(') and cron_expression.endswith(')'):
        cron_content = cron_expression[5:-1]
        try:
            AwsCroniter(cron_content)
            return cron_expression
        except Exception as e:
            logger.error(f"Invalid EventBridge cron expression: {e}")
            return None

    fields = cron_expression.split()
    if len(fields) != 5:
        return None

    minute, hour, day, month, day_of_week = fields

    # Convert day-of-week from standard (0=Sun) to EventBridge (1=Sun)
    if day_of_week != '*' and day_of_week != '?':
        dow_parts = []
        for part in day_of_week.split(','):
            part = part.strip()
            dow_parts.append(str(int(part) + 1) if part.isdigit() else part)
        day_of_week = ','.join(dow_parts)

    # EventBridge requires '?' for one of day-of-month / day-of-week
    if day_of_week == '*' and day == '*':
        day = '?'
    elif day_of_week != '*' and day_of_week != '?':
        day = '?'

    eb_cron = f"{minute} {hour} {day} {month} {day_of_week} *"
    try:
        AwsCroniter(eb_cron)
        return f"cron({eb_cron})"
    except Exception as e:
        logger.error(f"Invalid cron expression after conversion: {e}")
        return None


def _put_schedule(schedule_name, formatted_schedule, timezone_name, action, instance_id):
    """Create or update an EventBridge Scheduler schedule that invokes this
    Lambda to start/stop the instance."""
    lambda_arn = f"arn:aws:lambda:{aws_region}:{account_id}:function:{appName}-{envName}-ec2ActionValidator"
    target = {
        'Arn': lambda_arn,
        'RoleArn': scheduler_role_arn,
        'Input': json.dumps({"action": action, "instanceId": instance_id, "source": f"scheduled-{action}"})
    }
    params = {
        'Name': schedule_name,
        'GroupName': scheduler_group_name,
        'ScheduleExpression': formatted_schedule,
        'ScheduleExpressionTimezone': timezone_name or 'UTC',
        'FlexibleTimeWindow': {'Mode': 'OFF'},
        'State': 'ENABLED',
        'Target': target,
    }
    try:
        scheduler_client.update_schedule(**params)
        logger.info(f"{action} schedule updated for {instance_id}: {formatted_schedule} ({timezone_name})")
    except scheduler_client.exceptions.ResourceNotFoundException:
        scheduler_client.create_schedule(**params)
        logger.info(f"{action} schedule created for {instance_id}: {formatted_schedule} ({timezone_name})")


def _remove_schedule(schedule_name):
    """Remove an EventBridge Scheduler schedule if it exists."""
    try:
        scheduler_client.delete_schedule(Name=schedule_name, GroupName=scheduler_group_name)
        logger.info(f"Removed schedule {schedule_name}")
    except scheduler_client.exceptions.ResourceNotFoundException:
        pass
    except ClientError as e:
        logger.error(f"Error removing schedule {schedule_name}: {e}")


def _configure_schedule_shutdown(instance_id, arguments):
    """Configure or remove start/stop EventBridge Scheduler schedules."""
    timezone_name = arguments.get('timezone', 'UTC')

    stop_schedule = arguments.get('stopScheduleExpression')
    if stop_schedule:
        formatted = _format_schedule_expression(stop_schedule)
        if not formatted:
            raise ValueError(f"Invalid stop cron expression: {stop_schedule}")
        _put_schedule(f"shutdown-{instance_id}", formatted, timezone_name, "stopServer", instance_id)
    else:
        _remove_schedule(f"shutdown-{instance_id}")

    start_schedule = arguments.get('startScheduleExpression')
    if start_schedule:
        formatted = _format_schedule_expression(start_schedule)
        if not formatted:
            raise ValueError(f"Invalid start cron expression: {start_schedule}")
        _put_schedule(f"start-{instance_id}", formatted, timezone_name, "startServer", instance_id)
    else:
        _remove_schedule(f"start-{instance_id}")




def handle_update_server_config(instance_id, arguments):
    """Persist config and apply alarm + schedule shutdown settings."""
    logger.info(f"Updating config for {instance_id}")
    if not arguments:
        logger.error("Missing arguments for config update")
        return False
    try:
        if 'id' not in arguments:
            arguments['id'] = instance_id
        core_dyn.put_server_config(arguments)
        _configure_alarm_shutdown(instance_id, arguments)
        _configure_schedule_shutdown(instance_id, arguments)
        logger.info(f"Config updated successfully for {instance_id}")
        return True
    except Exception as e:
        logger.error(f"Config update failed for {instance_id}: {str(e)}", exc_info=True)
        return False


def handle_get_server_users(instance_id):
    """
    Helper function to handle get server users action using DynamoDB membership.
    Retrieves all users with access to a server, including their roles and full names from Cognito.
    
    Args:
        instance_id (str): EC2 instance ID
        
    Returns:
        list: List of ServerUsers objects with id, email, fullName, and role
        dict: Error response if operation fails
    """
    try:
        
        # Get all members for this server from DynamoDB
        members = core_dyn.list_server_members(instance_id)
        
        # Handle empty membership lists gracefully
        if not members:
            logger.info(f"No users found for server {instance_id}")
            return []
        
        # Convert to the expected ServerUsers format
        server_users = []
        for member in members:
            try:
                # Get user details from Cognito to get fullName
                user_info = auth.get_user_by_sub(member['userId'])
                full_name = user_info.get('fullName', member['email']) if user_info else member['email']
                
                server_users.append({
                    'id': member['userId'],
                    'email': member['email'],
                    'fullName': full_name,
                    'role': member['role']
                })
                
            except Exception as user_error:
                # If we can't get user details from Cognito, use email as fallback
                logger.warning(f"Could not get user details from Cognito for {member['userId']}: {str(user_error)}")
                server_users.append({
                    'id': member['userId'],
                    'email': member['email'],
                    'fullName': member['email'],  # Use email as fallback
                    'role': member['role']
                })
        
        logger.info(f"Retrieved {len(server_users)} users for server {instance_id} from DynamoDB")
        return server_users
    
    except Exception as e:
        logger.error("Error retrieving users for instance %s: %s", instance_id, str(e))
        return utl.response(500, {"error": f"Failed to retrieve users: {str(e)}"})

def handle_get_admin_users():
    """Helper function to get admin group members"""
    try:
        admin_group = os.getenv('ADMIN_GROUP_NAME', 'admin')
        users = auth.list_users_for_group(admin_group)
        return users
    except Exception as e:
        logger.error("Error retrieving admin users: %s", str(e))
        return []

def handle_search_user_by_email(email):
    """
    Helper function to search for a user by email address
    
    Args:
        email: Email address to search for
        
    Returns:
        dict: User information if found, None if not found, error response for exceptions
    """
    try:
        logger.info(f"Searching for user by email: {email}")
        
        # Use the existing find_user_by_email method from authHelper
        user_info = auth.find_user_by_email(email)
        
        if user_info is None:
            logger.info(f"No user found with email: {email}")
            # Return None for GraphQL nullable field instead of error response
            return None
        
        # Return user info in the expected ServerUsers format
        return {
            'id': user_info['sub'],
            'email': user_info['email'],
            'fullName': user_info['fullName']
        }
        
    except Exception as e:
        logger.error(f"Error searching for user by email {email}: {str(e)}", exc_info=True)
        return utl.response(500, {
            "error": "SEARCH_ERROR",
            "message": f"An error occurred while searching for user: {str(e)}"
        })

def action_process_sync(action, instance_id, arguments=None, user_email='system'):
    """Execute an action synchronously (read-only queries and write actions)."""
    logger.info("Action: %s InstanceId: %s", action, instance_id)
    action = action.lower().strip()

    # Read-only operations
    if action in ("getserverconfig",):
        return handle_get_server_config(instance_id)
    if action in ("getserverusers",):
        return handle_get_server_users(instance_id)

    # Start / stop
    if action in ("start", "startserver"):
        send_status_to_appsync("start", instance_id, "PROCESSING", "Starting server", user_email)
        ok = handle_server_action("start", instance_id, user_email)
        send_status_to_appsync("start", instance_id, "COMPLETED" if ok else "FAILED",
                               "Server starting" if ok else "Failed to start server", user_email)
        return utl.response(200 if ok else 500, {"msg" if ok else "err": "start"})

    if action in ("stop", "stopserver"):
        send_status_to_appsync("stop", instance_id, "PROCESSING", "Stopping server", user_email)
        ok = handle_server_action("stop", instance_id, user_email)
        send_status_to_appsync("stop", instance_id, "COMPLETED" if ok else "FAILED",
                               "Server stopping" if ok else "Failed to stop server", user_email)
        return utl.response(200 if ok else 500, {"msg" if ok else "err": "stop"})

    # Config (alarm + schedule)
    if action in ("putserverconfig", "updateserverconfig"):
        ok = handle_update_server_config(instance_id, arguments)
        return utl.response(200 if ok else 500, {"msg" if ok else "err": "config"})

    logger.error(f"Unknown action: {action}")
    return utl.response(400, {"err": f"Unknown action: {action}"})
    """
    Helper function to add a user to a server using DynamoDB membership with default viewer role
    
    Args:
        instance_id: EC2 instance ID
        user_email: Email address of the user to add
        user_attributes: Attributes of the user making the request (for created_by field)
        
    Returns:
        dict: Success response or error response with appropriate status code
    """
    try:
        logger.info(f"Adding user to server: email={user_email}, instance={instance_id}")
        
        # Step 1: Find user by email in Cognito to get their sub
        user_info = auth.find_user_by_email(user_email)
        
        if user_info is None:
            # ErrorHandler.log_error('USER_NOT_FOUND',
            #                      context={'operation': 'add_user_to_server', 'user_email': user_email, 'instance_id': instance_id},
            #                      email=user_email)
            # return ErrorHandler.create_error_response(404, 'USER_NOT_FOUND', email=user_email)
            logger.error('USER_NOT_FOUND')
            return None
        
        user_sub = user_info['sub']
        logger.info(f"User found in Cognito: email={user_email}, sub={user_sub}")
        
        # Step 2: Create DynamoDB membership record with default viewer role
        try:            
            # Create membership with default viewer role
            core_dyn.create_user_server_membership(
                user_id=user_sub,
                server_id=instance_id,
                role='viewer',  # Default role as per requirements
                permissions=['read']
            )
            
            logger.info(f"Successfully added user to server: email={user_email}, instance={instance_id}, role=viewer")
            return utl.response(200, {
                "message": f"Successfully added {user_email} to the server with viewer role",
                "user": {
                    "id": user_sub,
                    "email": user_info['email'],
                    "fullName": user_info['fullName'],
                    "role": "viewer"
                }
            })
            
        except ValueError as e:
            # Handle membership already exists error
            if "already exists" in str(e):
                # ErrorHandler.log_error('MEMBERSHIP_ALREADY_EXISTS',
                #                      context={'operation': 'add_user_to_server', 'user_email': user_email, 'instance_id': instance_id})
                # return ErrorHandler.create_error_response(409, 'MEMBERSHIP_ALREADY_EXISTS')
                logger.error('MEMBERSHIP_ALREADY_EXISTS')
                return None
            else:
                # ErrorHandler.log_error('VALIDATION_ERROR',
                #                      context={'operation': 'add_user_to_server', 'user_email': user_email, 'instance_id': instance_id},
                #                      exception=e, error=str(e))
                # return ErrorHandler.create_error_response(400, 'VALIDATION_ERROR', error=str(e))
                logger.error('VALIDATION_ERROR')
                return None
        
    except Exception:
        # ErrorHandler.log_error('INTERNAL_ERROR',
        #                      context={'operation': 'add_user_to_server', 'user_email': user_email, 'instance_id': instance_id},
        #                      exception=e, error=str(e))
        # return ErrorHandler.create_error_response(500, 'INTERNAL_ERROR', error=str(e))    
        logger.error('INTERNAL_ERROR')
        return None

def handle_update_user_role(instance_id, user_id, new_role, requesting_user_attributes):
    """
    Helper function to update a user's role for a server
    
    Args:
        instance_id: EC2 instance ID
        user_id: Cognito user sub of the user whose role to update
        new_role: New role to assign (admin, moderator, viewer)
        requesting_user_attributes: Attributes of the user making the request
        
    Returns:
        dict: Success response with updated membership or error response
    """
    try:
        requesting_user_email = requesting_user_attributes.get('email', 'unknown')
        requesting_user_sub = requesting_user_attributes.get('sub')
        
        logger.info(f"Updating user role: user={user_id}, instance={instance_id}, new_role={new_role}, requester={requesting_user_email}")
                
        # Step 1: Check if requesting user has admin permissions
        requesting_user_access = core_dyn.check_user_server_access(requesting_user_sub, instance_id)
        is_global_admin = core_dyn.check_global_admin(requesting_user_sub)
        
        if not (requesting_user_access and requesting_user_access['role'] == 'admin') and not is_global_admin:
            logger.warning(f"Unauthorized role update attempt: requester={requesting_user_email}")
            # return ErrorHandler.create_error_response(403, 'INSUFFICIENT_PERMISSIONS', 
            #                                         error="Insufficient permissions. Admin role required.")
            return None
        
        # Step 2: Prevent users from modifying their own admin role (lockout protection)
        if user_id == requesting_user_sub and requesting_user_access and requesting_user_access['role'] == 'admin' and new_role != 'admin':
            logger.warning(f"Self-admin-role modification blocked: user={requesting_user_email}")
            # return ErrorHandler.create_error_response(403, 'SELF_ADMIN_ROLE_PROTECTION', 
            #                                         error="Cannot modify your own admin role to prevent lockout")
            return None
        
        # Step 3: Update the user's role
        try:
            # Get current membership to preserve email
            current_membership = core_dyn.check_user_server_access(user_id, instance_id)
            # if not current_membership:
            #     return ErrorHandler.create_error_response(404, 'MEMBERSHIP_NOT_FOUND', 
            #                                             error="User membership not found")
            return None
            
            # Create new membership with updated role
            permissions_map = {'admin': ['read', 'write', 'delete', 'manage_users'], 'moderator': ['read', 'write', 'restart'], 'viewer': ['read']}
            updated_membership = core_dyn.create_user_server_membership(
                user_id=user_id,
                server_id=instance_id,
                role=new_role,
                permissions=permissions_map.get(new_role, ['read'])
            )
            
            logger.info(f"Successfully updated user role: user={user_id}, instance={instance_id}, new_role={new_role}")
            
            # Return UserMembership object directly for GraphQL
            return {
                "userId": user_id,
                "serverId": instance_id,
                "email": current_membership.get('email', ''),
                "role": new_role,
                "createdAt": updated_membership['createdAt'],
                "updatedAt": updated_membership['updatedAt'],
                "createdBy": updated_membership['createdBy']
            }
            
        except ValueError as e:
            if "not found" in str(e):
                logger.error(f"User membership not found for role update: user={user_id}, instance={instance_id}")
                # return ErrorHandler.create_error_response(404, 'USER_MEMBERSHIP_NOT_FOUND', 
                #                                         error=f"User membership not found for server {instance_id}")
                return None
            else:
                logger.error(f"Validation error updating role: {str(e)}")
                # return ErrorHandler.create_error_response(400, 'VALIDATION_ERROR', error=str(e))
                return None
        
    except Exception as e:
        logger.error(f"Error updating user role: user={user_id}, instance={instance_id}, error={str(e)}", exc_info=True)
        # return ErrorHandler.create_error_response(500, 'INTERNAL_ERROR', error=f"An unexpected error occurred: {str(e)}")

def handle_remove_user_from_server(instance_id, user_id, requesting_user_attributes):
    """
    Helper function to remove a user from a server
    
    Args:
        instance_id: EC2 instance ID
        user_id: Cognito user sub of the user to remove
        requesting_user_attributes: Attributes of the user making the request
        
    Returns:
        dict: Success response or error response
    """
    try:
        requesting_user_email = requesting_user_attributes.get('email', 'unknown')
        requesting_user_sub = requesting_user_attributes.get('sub')
        
        logger.info(f"Removing user from server: user={user_id}, instance={instance_id}, requester={requesting_user_email}")
        
        # Step 1: Check if requesting user has admin permissions
        requesting_user_access = core_dyn.check_user_server_access(requesting_user_sub, instance_id)
        is_global_admin = core_dyn.check_global_admin(requesting_user_sub)
        
        if not (requesting_user_access and requesting_user_access['role'] == 'admin') and not is_global_admin:
            logger.warning(f"Unauthorized user removal attempt: requester={requesting_user_email}")
            # return ErrorHandler.create_error_response(403, 'INSUFFICIENT_PERMISSIONS', 
            #                                         error="Insufficient permissions. Admin role required.")
            return None
        
        # Step 2: Prevent users from removing themselves if they're the only admin (lockout protection)
        if user_id == requesting_user_sub and requesting_user_access and requesting_user_access['role'] == 'admin':
            # Check if there are other admins
            all_members = core_dyn.list_server_members(instance_id)
            admin_members = [m for m in all_members if m.get('role') == 'admin']
            if len(admin_members) <= 1:
                logger.warning(f"Self-removal blocked - only admin: user={requesting_user_email}")
                # return ErrorHandler.create_error_response(403, 'LAST_ADMIN_PROTECTION', 
                #                                         error="Cannot remove yourself as you are the only admin for this server")
                return None
        
        # Step 3: Remove the user's membership
        try:
            deleted_membership = core_dyn.delete_membership(user_id, instance_id)
            
            logger.info(f"Successfully removed user from server: user={user_id}, instance={instance_id}")
            
            # Return JSON response as expected by GraphQL AWSJSON type
            return {
                "message": "Successfully removed user from server",
                "removedUser": {
                    "userId": deleted_membership['userId'],
                    "email": deleted_membership['email'],
                    "role": deleted_membership['role']
                }
            }
            
        except ValueError as e:
            if "not found" in str(e):
                logger.error(f"User membership not found for removal: user={user_id}, instance={instance_id}")
                # return ErrorHandler.create_error_response(404, 'USER_MEMBERSHIP_NOT_FOUND', 
                #                                         error=f"User membership not found for server {instance_id}")
                return None
            else:
                # logger.error(f"Validation error removing user: {str(e)}")
                # return ErrorHandler.create_error_response(400, 'VALIDATION_ERROR', error=str(e))
                return None
        
    except Exception as e:
        logger.error(f"Error removing user from server: user={user_id}, instance={instance_id}, error={str(e)}", exc_info=True)
        return None
        #return ErrorHandler.create_error_response(500, 'INTERNAL_ERROR', error=f"An unexpected error occurred: {str(e)}")

def handle_get_server_uptime(instance_id, days=30):
    """Return start/stop history plus computed uptime for an instance.

    Reads 'state:*' transition rows (written by ec2StateHandler for all
    origins) from the audit table, computes current-session and total running
    seconds over the requested window.
    """
    try:
        now_epoch = int(time.time())
        days = days if (days and days > 0) else 30
        since_epoch = now_epoch - days * 86400

        events = audit_table.query_state_history(instance_id, since_epoch=since_epoch, limit=500)
        total_seconds = audit_table.compute_running_seconds(events, now_epoch)

        # Current state + current-session uptime from the newest event
        current_state = None
        current_session_seconds = 0
        if events:
            newest = events[0]  # query returns newest first
            current_state = newest['state']
            if newest['state'] == 'running':
                current_session_seconds = max(0, now_epoch - newest['timestamp'])

        return {
            'id': instance_id,
            'state': current_state,
            'currentSessionSeconds': current_session_seconds,
            'totalRunningSeconds': total_seconds,
            'windowDays': days,
            'events': events
        }
    except Exception as e:
        logger.error(f"Error getting uptime for {instance_id}: {str(e)}", exc_info=True)
        return utl.response(500, {"error": f"Failed to get uptime: {str(e)}"})

def handle_get_server_config(instance_id):
    """Helper function to handle get server config action"""
    try:

        server_config = core_dyn.get_server_config(instance_id)
        if server_config is None:
            logger.warning(f"No config found for instance {instance_id}, returning empty config")
            # Return empty config structure matching GraphQL schema
            return {
                'id': instance_id,
                'stopScheduleExpression': '',
                'startScheduleExpression': '',
                'alarmMetric': 'CPUUtilization',
                'alarmThreshold': 0.0,
                'alarmEvaluationPeriod': 0,
                'timezone': 'UTC',
                'hasCognitoGroup': False
            }
        return server_config
    except Exception as e:
        logger.error(f"Error getting server config for {instance_id}: {str(e)}", exc_info=True)
        return utl.response(500, {"error": f"Failed to get server config: {str(e)}"})

def handle_local_invocation(event, context):
    # Scheduled EventBridge invocations (start/stop) run as 'system'
    return action_process_sync(event["action"], event["instanceId"], user_email='system')

def handle_search_user_by_email_operation(event):
    """Handle searchUserByEmail operation."""
    email = event["arguments"].get("email")
    if not email:
        return utl.response(400, {"err": "email is required"})
    return handle_search_user_by_email(email)

def route_instance_operation(event, instance_id, user_attributes):
    """Route operations that require instance authorization with role-based permissions."""
    field_name = event["info"]["fieldName"].lower()
    
    # Prepare input data
    input_data = None
    if input_args := event["arguments"].get("input"):
        input_data = {**input_args, 'id': instance_id}
    
    # Determine minimum permission based on server operation
    required_permission = 'manage_server'  # Default for server operations (stop/start, config and user mngmt)
        
    # Check authorization with role-based permissions
    try:
        is_authorized, user_role, auth_reason = core_dyn.check_user_authorization(user_attributes['sub'], instance_id, required_permission)
        if not is_authorized:
            logger.error(f"User {user_attributes['email']} not authorized for {field_name} on instance {instance_id}: {auth_reason}")
            return utl.response(403, {"err": f"Insufficient permissions. {auth_reason}"})
        
        logger.info(f"Authorization granted: user={user_attributes['email']}, role={user_role}, operation={field_name}")
        
    except Exception as e:
        logger.error(f"Authorization check FAILED: user={user_attributes.get('email', 'unknown')}, instance={instance_id}, error={str(e)}", exc_info=True)
        return utl.response(500, {"err": "Authorization check failed"})
    
    # Read-only operations
    if field_name in ["getserverconfig", "getserverusers"]:
        return action_process_sync(field_name, instance_id, input_data, user_attributes["email"])

    # Uptime history (read-only)
    if field_name == "getserveruptime":
        days = event["arguments"].get("days") or 30
        return handle_get_server_uptime(instance_id, days)
    
    # Add user operation - create DynamoDB membership with default viewer role
    if field_name == "addusertoserver":
        user_email = event["arguments"].get("userEmail")
        if not user_email:
            return utl.response(400, {"err": "userEmail is required"})
        return handle_add_user_to_server(instance_id, user_email, user_attributes)
    
    # Update user role operation
    if field_name == "updateuserrole":
        user_id = event["arguments"].get("userId")
        new_role = event["arguments"].get("role")
        if not user_id or not new_role:
            return utl.response(400, {"err": "userId and role are required"})
        return handle_update_user_role(instance_id, user_id, new_role, user_attributes)
    
    # Remove user from server operation
    if field_name == "removeuserfromserver":
        user_id = event["arguments"].get("userId")
        if not user_id:
            return utl.response(400, {"err": "userId is required"})
        return handle_remove_user_from_server(instance_id, user_id, user_attributes)
    
    # Config operations (synchronous): persist + apply alarm/schedule, return config data
    if field_name in ["putserverconfig", "updateserverconfig"]:
        result = action_process_sync(field_name, instance_id, input_data, user_attributes["email"])
        if isinstance(result, dict) and result.get('statusCode') == 200:
            return input_data
        return result
    
    # Start / stop (synchronous)
    return action_process_sync(field_name, instance_id, input_data, user_attributes["email"])

def handler(event, context):
    """
    Main handler for ec2ActionValidator Lambda
    Validates authorization and routes requests to appropriate handlers
    """
    try:
        # Check if this is an EventBridge scheduled event (no auth required)
        if "action" in event and "instanceId" in event and "source" in event:
            logger.info(f"EventBridge scheduled event detected: action={event['action']}, instance={event['instanceId']}")
            return handle_local_invocation(event, context)
        
        # Handle local invocation or extract token
        try:
            token = utl.extract_auth_token(event)
        except ValueError as e:
            if str(e) == "Local invocation detected":
                return handle_local_invocation(event, context)
            logger.error(str(e))
            return utl.response(401, {"err": str(e)})
        
        # Validate token and get user
        try:
            user_attributes = auth.process_token(token)
        except ValueError as e:
            return utl.response(401, {"err": str(e)})
        
        # Validate arguments exist
        if not event.get("arguments"):
            logger.error("No arguments found in the event")
            return utl.response(400, {"err": "No arguments found in the event"})

        logger.info(event)
        
        # Handle special operations (no instance ID required)
        field_name = event["info"]["fieldName"].lower()
        
        if field_name == "searchuserbyemail":
            return handle_search_user_by_email_operation(event)
        
        if field_name == "getadminusers":
            return handle_get_admin_users()
        
        # Extract instance ID for regular operations
        instance_id = ec2Helper.extract_instance_id(event)
        if not instance_id:
            logger.error("Instance id is not present in the event")
            return utl.response(400, {"err": "Instance id is not present in the event"})
        
        logger.info("Received instanceId: %s", instance_id)
        logger.info("Received field name: %s for user %s", event["info"]["fieldName"], user_attributes["email"])
        
        # Route instance operations        
        return route_instance_operation(event, instance_id, user_attributes)
        
    except Exception as e:
        logger.error("Unexpected error in handler: %s", e, exc_info=True)
        return utl.response(500, {"err": "Internal server error"})