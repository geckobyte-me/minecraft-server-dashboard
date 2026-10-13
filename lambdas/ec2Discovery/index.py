import boto3
import logging
import os
import concurrent.futures
import authHelper
import ec2Helper
import ddbHelper
import pytz
# from errorHandler import ErrorHandler

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ec2_client = boto3.client('ec2')
ec2 = boto3.resource('ec2')
cognito_idp = boto3.client('cognito-idp')
lambda_client = boto3.client('lambda')
ENCODING = 'utf-8'

appValue = os.getenv('TAG_APP_VALUE')
cognito_pool_id = os.getenv('COGNITO_USER_POOL_ID')
servers_table_name = os.getenv('SERVERS_TABLE_NAME')
fix_server_role_lambda = os.getenv('FIX_SERVER_ROLE_LAMBDA_ARN')

auth = authHelper.Auth(cognito_pool_id)
ec2_utils = ec2Helper.Ec2Utils()
ddb = ddbHelper.CoreTableDyn(servers_table_name)
utc = pytz.utc
pst = pytz.timezone('US/Pacific')

def get_user_instances(user_sub, app_value):
    """Get instances based on user permissions using DynamoDB membership."""
    try:
        core_dyn = ddbHelper.CoreTableDyn()
        
        # Check if user has global admin role
        if core_dyn.check_global_admin(user_sub):
            logger.info(f"Global admin user - listing all instances by app tag: {app_value}")
            user_instances = ec2_utils.list_instances_by_app_tag(app_value)
            logger.info(f"Found {user_instances['TotalInstances']} instances with App={app_value}")
            return user_instances
        
        # Get user server memberships
        user_memberships = core_dyn.list_user_servers(user_sub)
        
        if not user_memberships:
            logger.info(f"User has no server memberships: {user_sub}")
            return {
                "Instances": [],
                "TotalInstances": 0
            }

        # Get instances for servers where user has membership
        server_ids = [membership['serverId'] for membership in user_memberships]
        logger.info(f"User has membership for servers: {server_ids}")
        
        # Get instance details for each server the user has access to
        user_instances = []
        for server_id in server_ids:
            try:
                response = ec2_client.describe_instances(InstanceIds=[server_id])
                if response["Reservations"]:
                    for reservation in response["Reservations"]:
                        for instance in reservation["Instances"]:
                            # Verify instance has the correct app tag
                            tags = instance.get('Tags', [])
                            app_tag = next((tag['Value'] for tag in tags if tag['Key'] == 'App'), None)
                            if app_tag == app_value:
                                user_instances.append(instance)
                            else:
                                logger.warning(f"Instance {server_id} does not have App={app_value} tag")
            except Exception as e:
                logger.error(f"Error getting instance {server_id}: {str(e)}")
                continue
        
        total_instances = len(user_instances)
        logger.info(f"Found {total_instances} instances for user memberships")
        
        return {
            "Instances": user_instances,
            "TotalInstances": total_instances
        }
            
    except Exception as e:
        logger.error(f"Error retrieving user instances: {str(e)}")
        raise ValueError(f"Error retrieving user instances: {str(e)}")

def fetch_parallel_data(instances):
    """Fetch instance data in parallel using ThreadPoolExecutor."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        instance_types = {executor.submit(ec2_client.describe_instance_types, InstanceTypes=[instance['InstanceType']]): instance for instance in instances}
        instance_status = {executor.submit(ec2_utils.describe_instance_status, instance['InstanceId']): instance for instance in instances}

    return instance_types, instance_status

def auto_fix_iam_if_needed(instance_id, iam_status):
    """Automatically trigger IAM fix if status is not 'ok'."""
    if iam_status != 'ok' and fix_server_role_lambda:
        try:
            logger.info(f"Auto-fixing IAM for instance {instance_id}")
            lambda_client.invoke(
                FunctionName=fix_server_role_lambda,
                InvocationType='Event',  # Async invocation
                Payload=f'{{"instanceId": "{instance_id}"}}'
            )
        except Exception as e:
            logger.error(f"Failed to trigger IAM fix for {instance_id}: {str(e)}")

def build_server_response(server, instance_types, instance_status, user_email):
    """Build individual server response object."""
    instance_id = server['InstanceId']
    
    # Get parallel execution results
    instance_type_future = next(future for future in instance_types if future.result()['InstanceTypes'][0]['InstanceType'] == server['InstanceType'])
    instance_type = instance_type_future.result()
    instance_status_future = next(future for future in instance_status if future.result()['instanceId'] == instance_id)
    status = instance_status_future.result()

    # Auto-fix IAM if needed
    iam_status = status['iamStatus'].lower()
    auto_fix_iam_if_needed(instance_id, iam_status)

    # Extract server details
    instance_name = next((tag['Value'] for tag in server['Tags'] if tag['Key'] == 'Name'), 'Undefined')
    public_ip = server['NetworkInterfaces'][0].get('Association', {}).get('PublicIp', 'none')
    vcpus = instance_type['InstanceTypes'][0]['VCpuInfo']['DefaultVCpus']
    memory_info = instance_type['InstanceTypes'][0]['MemoryInfo']['SizeInMiB']
    volume_id = server['BlockDeviceMappings'][0]['Ebs']['VolumeId']
    volume = ec2.Volume(volume_id)

    pst_launch_time = server["LaunchTime"].astimezone(pst)

    return {
        'id': instance_id,
        'name': instance_name,
        'userEmail': user_email,
        'type': server['InstanceType'],
        'state': server['State']['Name'].lower(),
        'vCpus': vcpus,
        'memSize': memory_info,
        'diskSize': volume.size,
        'publicIp': public_ip,
        'iamStatus': 'fixing' if iam_status != 'ok' else 'ok',
        'launchTime': pst_launch_time.strftime("%m/%d/%Y - %H:%M:%S")
    }

def handler(event, context): 
    try:
        # Extract and validate token
        token = auth.extract_auth_token(event)
        user_attributes = auth.process_token(token)
        
        # Get user sub for DynamoDB membership queries
        user_sub = user_attributes.get('sub')

        # Get user instances using DynamoDB membership
        user_instances = get_user_instances(user_sub, appValue)
        
        if user_instances["TotalInstances"] == 0:
            logger.info(f"No servers found for user {user_sub}")
            return []
        
        # Fetch data in parallel
        instances = user_instances["Instances"]
        instance_types, instance_status = fetch_parallel_data(instances)
        
        # Build response
        result = []
        for server in instances:
            server_data = build_server_response(server, instance_types, instance_status, user_attributes['email'])
            result.append(server_data)

        logger.info(result)
        return result

    except Exception as e:
        return f"Error processing request: {e}"
