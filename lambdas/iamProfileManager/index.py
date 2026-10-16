import logging
import os
import authHelper
import ec2Helper
import utilHelper

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ec2_utils = ec2Helper.Ec2Utils()
utl = utilHelper.Utils()

# Environment variables
cognito_pool_id = os.getenv('COGNITO_USER_POOL_ID')

auth = authHelper.Auth(cognito_pool_id)


class IamProfile:
    """Manages IAM instance profile association for EC2 instances.

    Thin wrapper that delegates the association lifecycle to
    ec2Helper.Ec2Utils, which owns the canonical implementation.
    """

    def __init__(self, instance_id):
        self.instance_id = instance_id

    def manage_iam_profile(self):
        """
        Ensure instance has the correct IAM profile attached.

        Returns:
            dict: Status response with success/error information
        """
        return ec2_utils.ensure_iam_profile(self.instance_id)


def check_authorization(event, instance_id, user_attributes):
    """
    Check if user is authorized to perform actions on instance
    
    Args:
        event: Lambda event object containing identity info
        instance_id: EC2 instance ID
        user_attributes: Dict containing user info including email
        
    Returns:
        bool: True if authorized, False if not
    """
    cognito_groups = event["identity"].get("groups", [])
    user_email = user_attributes["email"]
    
    logger.info(f"Authorization check: user={user_email}, instance={instance_id}, groups={cognito_groups}")
    
    try:
        is_authorized, auth_reason = utl.check_user_authorization(
            cognito_groups, 
            instance_id, 
            user_email, 
            ec2_utils
        )
        
        if is_authorized:
            logger.info(f"Authorization SUCCESS: {auth_reason} - {user_email} authorized for {instance_id}")
        else:
            logger.warning(f"Authorization DENIED: User {user_email} not authorized for {instance_id}")
        
        return is_authorized
        
    except Exception as e:
        logger.error(f"Authorization check FAILED: user={user_email}, instance={instance_id}, error={str(e)}", exc_info=True)
        raise


def _extract_token(event):
    """Extract authorization token from event"""
    try:
        return event['request']['headers']['authorization']
    except KeyError:
        # No token for internal invocations
        return None

def _validate_arguments(event):
    """Validate and extract instance_id from arguments"""
    # Check for direct invocation (from ec2Discovery)
    if 'instanceId' in event:
        return event['instanceId']
    
    # Check for AppSync invocation
    arguments = event.get("arguments")
    if not arguments:
        raise ValueError("No arguments found in the event")
    
    instance_id = arguments.get("instanceId")
    if not instance_id:
        raise ValueError("Instance id is not present in the event")
    
    return instance_id

def _authenticate_user(token):
    """Authenticate user and return user attributes"""
    user_attributes = auth.process_token(token)
    if not user_attributes:
        raise ValueError("Invalid token")
    return user_attributes

def _fix_iam_role(instance_id):
    """Fix IAM role for instance"""
    iam_profile = IamProfile(instance_id)
    result = iam_profile.manage_iam_profile()
    
    if result.get("status") != "success":
        error_msg = result.get("message", "Unknown error")
        raise Exception(error_msg)
    
    return result.get("message")

def handler(event, context):
    """Main handler for iamProfileManager Lambda"""
    logger.info("iamProfileManager Lambda invoked")
    
    try:
        # Extract and validate inputs
        token = _extract_token(event)
        instance_id = _validate_arguments(event)
        
        # Skip authorization for internal invocations
        if token:
            user_attributes = _authenticate_user(token)
            logger.info(f"Processing iamProfileManager: instance={instance_id}, user={user_attributes['email']}")
            
            # Check authorization
            if not check_authorization(event, instance_id, user_attributes):
                logger.error(f"User {user_attributes['email']} not authorized for {instance_id}")
                return utl.response(401, {"err": "User not authorized"})
        else:
            logger.info(f"Processing iamProfileManager (internal): instance={instance_id}")
        
        # Fix IAM role
        message = _fix_iam_role(instance_id)
        logger.info(f"IAM role fix SUCCESS: {message}")
        return utl.response(200, {"msg": message, "success": True})
        
    except ValueError as e:
        logger.error(f"Validation error: {str(e)}")
        return utl.response(400, {"err": str(e)})
    except Exception as e:
        logger.error(f"IAM role fix FAILED: {str(e)}", exc_info=True)
        return utl.response(500, {"err": f"Failed to fix IAM role: {str(e)}", "success": False})
