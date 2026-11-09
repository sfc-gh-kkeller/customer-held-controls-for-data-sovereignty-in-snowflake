"""
On-Premise TCP Proxy Agent
Generic TCP proxy that tunnels traffic to any on-premise service
Initiates outbound WebSocket connection (firewall-friendly)
"""

import asyncio
import websockets
import json
import os
import logging
import requests
import time
import base64
from typing import Dict, Any, Optional
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OnPremiseAgent:
    """Generic TCP Proxy Agent - Forwards traffic to any on-premise service"""
    
    def __init__(self):
        # Snowflake connection
        self.snowflake_url = os.getenv('SNOWFLAKE_URL', 'ws://localhost:8081')
        self.snowflake_account = os.getenv('SNOWFLAKE_ACCOUNT')
        self.snowflake_account_url = os.getenv('SNOWFLAKE_ACCOUNT_URL')
        self.snowflake_role = os.getenv('SNOWFLAKE_ROLE')
        self.snowflake_user = os.getenv('SNOWFLAKE_USER')  # Optional, for logging/reference only
        self.snowflake_pat = os.getenv('SNOWFLAKE_PAT')
        self.snowflake_token = None
        
        # No default target - routing is controlled by Snowflake agent
        # The agent is a pure TCP proxy without configuration
        
        # Database credentials (temporary - for query forwarding mode)
        self.db_name = os.getenv('DB_NAME', 'test_db')
        self.db_user = os.getenv('DB_USER', 'postgres')
        self.db_password = os.getenv('DB_PASSWORD', '')
        
        # Authentication
        # Fallback auth when SNOWFLAKE_PAT is not set (e.g. local dev without Snowflake)
        self.handshake_secret = os.getenv('HANDSHAKE_SECRET', 'test-secret-key')
        
        # WebSocket connection
        self.websocket = None
        self.aes_key = None
        
        # RSA key pair for secure key exchange
        self.rsa_private_key = None
        self.rsa_public_key = None
        
        # Connection state
        self.connected = False
        
        # Session management (for persistent connections)
        self.sessions = {}  # session_id -> {'reader': reader, 'writer': writer, 'task': task}
        self.pending_session_data = {}  # session_id -> [messages] - buffer for early data
        
        # Connection pool for reusing TCP connections (session caching)
        self.connection_pool = {}  # f"{host}:{port}" -> list of {'reader': reader, 'writer': writer, 'last_used': time}
        self.pool_max_size = 10  # Max pooled connections per target
        self.pool_idle_timeout = 300  # 5 minutes idle timeout
        self.pool_cleanup_task = None
        
    async def start(self):
        """Start the agent"""
        # Load port mappings
        self.port_mappings = self.load_port_mappings()
        
        logger.info("Configuration loaded:")
        logger.info(f"  Snowflake URL: {self.snowflake_url}")
        logger.info(f"  Snowflake Account: {self.snowflake_account}")
        logger.info(f"  Snowflake User: {self.snowflake_user if self.snowflake_user else 'Not specified'}")
        logger.info(f"  Auth Mode: {'PAT Token' if self.snowflake_pat else 'Shared Secret'}")
        logger.info(f"  Port Mappings: {len(self.port_mappings)} configured")
        
        logger.info("=" * 60)
        logger.info("  ON-PREMISE TCP PROXY AGENT")
        logger.info("=" * 60)
        logger.info(f"🔌 Snowflake URL: {self.snowflake_url}")
        logger.info(f"🌐 Port Forwarding Mode")
        logger.info(f"🎯 Port Mappings:")
        for mapping in self.port_mappings:
            logger.info(f"   {mapping['local_port']} → {mapping['remote_host']}:{mapping['remote_port']}")
        
        # Start connection pool cleanup task
        self.pool_cleanup_task = asyncio.create_task(self.cleanup_idle_connections())
        logger.info("♻️  Connection pool enabled with 5-minute idle timeout")
        
        # Connect to Snowflake and handle messages
        await self.connect_to_snowflake()
    
    def load_port_mappings(self):
        """Load port mappings from JSON file"""
        import json
        import os
        
        # Allow override via environment variable
        config_filename = os.getenv('PORT_MAPPINGS_FILE', 'port_mappings.json')
        config_file = os.path.join(os.path.dirname(__file__), config_filename)
        
        if os.path.exists(config_file):
            logger.info(f"📋 Loading port mappings from: {config_filename}")
            with open(config_file, 'r') as f:
                config = json.load(f)
                return config.get('mappings', [])
        else:
            # Default mapping
            logger.warning(f"⚠️  No {config_filename} found, using defaults")
            return [
                {
                    'local_port': 5432,
                    'remote_host': 'localhost',
                    'remote_port': 5432,
                    'description': 'PostgreSQL (default)'
                }
            ]
    
    async def get_pooled_connection(self, host: str, port: int):
        """Get a connection from the pool or create a new one"""
        pool_key = f"{host}:{port}"
        
        # Check if pool exists for this target
        if pool_key in self.connection_pool and len(self.connection_pool[pool_key]) > 0:
            logger.info(f"🔍 Pool has {len(self.connection_pool[pool_key])} connections for {host}:{port}")
            # Get connection from pool
            conn_info = self.connection_pool[pool_key].pop(0)
            reader, writer = conn_info['reader'], conn_info['writer']
            
            # Check if connection is still alive
            try:
                # Test if writer is still open
                if writer.is_closing():
                    raise Exception("Connection closed")
                
                logger.info(f"✅ REUSING pooled connection to {host}:{port} (pool now: {len(self.connection_pool.get(pool_key, []))})")
                return reader, writer
            except Exception as e:
                # Connection is dead, create a new one
                logger.warning(f"🔄 Pooled connection to {host}:{port} is dead ({e}), creating new one")
                pass
        else:
            logger.info(f"🔍 Pool empty or missing for {host}:{port}")
        
        # Create new connection
        logger.info(f"🔌 Creating new connection to {host}:{port}")
        reader, writer = await asyncio.open_connection(host, port)
        
        # Enable TCP keep-alive to prevent idle connection drops
        sock = writer.get_extra_info('socket')
        if sock:
            import socket
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            # Set keep-alive parameters (platform-specific)
            if hasattr(socket, 'TCP_KEEPIDLE'):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)  # Start after 60s idle
            if hasattr(socket, 'TCP_KEEPINTVL'):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)  # Probe every 10s
            if hasattr(socket, 'TCP_KEEPCNT'):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 6)     # 6 failed probes = dead
        
        return reader, writer
    
    async def return_to_pool(self, host: str, port: int, reader, writer):
        """Return a connection to the pool for reuse"""
        pool_key = f"{host}:{port}"
        
        # Initialize pool for this target if it doesn't exist
        if pool_key not in self.connection_pool:
            self.connection_pool[pool_key] = []
        
        # Check pool size limit
        if len(self.connection_pool[pool_key]) >= self.pool_max_size:
            # Pool is full, close the connection
            logger.debug(f"🗑️  Pool full for {host}:{port}, closing connection")
            writer.close()
            await writer.wait_closed()
            return
        
        # Check if connection is still alive
        if writer.is_closing():
            logger.debug(f"🗑️  Connection to {host}:{port} is closed, not pooling")
            return
        
        # Add to pool
        self.connection_pool[pool_key].append({
            'reader': reader,
            'writer': writer,
            'last_used': time.time()
        })
        logger.info(f"✅ Returned connection to pool for {host}:{port} (pool size: {len(self.connection_pool[pool_key])})")
    
    async def cleanup_idle_connections(self):
        """Background task to clean up idle connections in the pool"""
        while True:
            try:
                await asyncio.sleep(60)  # Run every minute
                
                current_time = time.time()
                total_closed = 0
                
                for pool_key, connections in list(self.connection_pool.items()):
                    # Filter out idle connections
                    active_connections = []
                    for conn_info in connections:
                        if current_time - conn_info['last_used'] < self.pool_idle_timeout:
                            active_connections.append(conn_info)
                        else:
                            # Close idle connection
                            try:
                                conn_info['writer'].close()
                                await conn_info['writer'].wait_closed()
                                total_closed += 1
                            except:
                                pass
                    
                    # Update pool
                    if active_connections:
                        self.connection_pool[pool_key] = active_connections
                    else:
                        # Remove empty pool
                        del self.connection_pool[pool_key]
                
                if total_closed > 0:
                    logger.debug(f"🧹 Cleaned up {total_closed} idle connections from pool")
                    
            except Exception as e:
                logger.error(f"❌ Error in pool cleanup: {e}")
    
    async def connect_to_snowflake(self):
        """Connect to Snowflake WebSocket server with infinite retry for resilience"""
        retry_delay = 5
        max_retry_delay = 300  # Cap at 5 minutes
        attempt = 0
        
        # Use SSL for wss:// connections
        import ssl
        ssl_context = ssl.create_default_context()
        if self.snowflake_url.startswith('ws://'):
            ssl_context = None
        
        # Exchange PAT for Snowflake Token if PAT is provided
        if self.snowflake_pat and not self.snowflake_token:
            logger.info("🔐 Exchanging PAT for Snowflake Token...")
            self.snowflake_token = await self.exchange_pat_for_token()
        
        # Infinite retry loop for resilience (handles Snowflake 30-day restarts)
        while True:
            attempt += 1
            try:
                logger.info(f"🔄 Connecting to Snowflake... (attempt {attempt})")
                
                # Prepare headers with Snowflake Token if available
                headers = {}
                if self.snowflake_token:
                    # Extract access_token if JSON response
                    token = self.snowflake_token
                    if token.startswith('{'):
                        import json as json_lib
                        try:
                            token_data = json_lib.loads(token)
                            token = token_data.get('access_token', token)
                        except:
                            pass
                    
                    headers['Authorization'] = f'Snowflake Token="{token}"'
                    logger.info("🔐 Connecting with Snowflake Token authentication")
                
                # Convert headers to list of tuples for websockets
                additional_headers = None
                if headers:
                    additional_headers = [(k, v) for k, v in headers.items()]
                
                # Connect with compression for better performance
                self.websocket = await websockets.connect(
                    self.snowflake_url,
                    ssl=ssl_context,
                    compression='deflate',  # Enable per-message deflate compression
                    ping_interval=30,
                    ping_timeout=10,
                    max_size=10 * 1024 * 1024,  # 10MB max message size
                    additional_headers=additional_headers
                )
                
                logger.info("✅ WebSocket connection established")
                
                # Perform handshake
                await self.perform_handshake()
                
                self.connected = True
                
                # Push port mappings to tunnel sidecar
                await self.push_port_mappings()
                
                logger.info("✅ Agent authenticated and ready")
                logger.info("📡 Listening for forwarding requests...")
                
                # Reset retry delay on successful connection
                retry_delay = 5
                attempt = 0
                
                # Handle incoming messages (blocks until disconnect)
                await self.handle_messages()
                
                # If we get here, connection was lost - will auto-reconnect
                logger.warning("⚠️  Connection lost, will reconnect...")
                
            except Exception as e:
                logger.error(f"❌ Connection failed: {e}")
                self.connected = False
                
                # Exponential backoff with cap
                logger.info(f"⏳ Retrying in {retry_delay} seconds... (auto-reconnect enabled for Snowflake restarts)")
                await asyncio.sleep(retry_delay)
                
                # Increase retry delay (exponential backoff)
                retry_delay = min(retry_delay * 2, max_retry_delay)
    
    async def exchange_pat_for_token(self) -> str:
        """Exchange PAT for Snowflake Token"""
        try:
            # Build the scope
            endpoint = self.snowflake_url.replace('wss://', '').replace('ws://', '')
            scope_role = f'session:role:{self.snowflake_role}' if self.snowflake_role else None
            scope = f'{scope_role} {endpoint}' if scope_role else endpoint
            
            # Prepare token exchange request
            data = {
                'grant_type': 'urn:ietf:params:oauth:grant-type:token-exchange',
                'scope': scope,
                'subject_token': self.snowflake_pat,
                'subject_token_type': 'programmatic_access_token'
            }
            
            # Build OAuth URL
            if self.snowflake_account_url:
                url = f'{self.snowflake_account_url}/oauth/token'
            else:
                url = f'https://{self.snowflake_account}.snowflakecomputing.com/oauth/token'
            
            logger.info(f"🔐 Token exchange URL: {url}")
            logger.info(f"🔐 Token scope: {scope}")
            
            # Make request
            response = requests.post(url, data=data)
            
            if response.status_code == 200:
                logger.info("✅ Token exchange successful")
                return response.text
            else:
                logger.error(f"❌ Token exchange failed: {response.status_code} - {response.text}")
                raise Exception(f"Token exchange failed with status {response.status_code}")
                
        except Exception as e:
            logger.error(f"❌ Failed to exchange PAT for token: {e}")
            raise
    
    async def perform_handshake(self):
        """Perform authentication handshake with RSA key exchange"""
        logger.info("🤝 Performing secure handshake with RSA key exchange...")
        
        # Step 1: Generate RSA key pair (2048-bit)
        logger.info("🔑 Generating RSA key pair...")
        self.rsa_private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        self.rsa_public_key = self.rsa_private_key.public_key()
        
        # Step 2: Serialize public key to PEM format for transmission
        public_key_pem = self.rsa_public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        public_key_b64 = base64.b64encode(public_key_pem).decode('utf-8')
        
        # Step 3: Send handshake with public key and authentication
        handshake_msg = {
            'type': 'handshake',
            'agent_type': 'tcp_proxy',
            'public_key': public_key_b64  # Send RSA public key
        }
        
        if self.snowflake_token:
            # Token is in headers for ingress, and also in message for app-level auth
            token = self.snowflake_token
            if token.startswith('{'):
                import json as json_lib
                try:
                    token_data = json_lib.loads(token)
                    token = token_data.get('access_token', token)
                except:
                    pass
            handshake_msg['snowflake_token'] = token
            logger.info("🔐 Authenticating with Snowflake Token + RSA public key")
        else:
            handshake_msg['secret'] = self.handshake_secret
            logger.info("🔐 Authenticating with Shared Secret + RSA public key")
        
        await self.websocket.send(json.dumps(handshake_msg))
        
        # Step 4: Receive response with wrapped AES key
        response = await self.websocket.recv()
        response_data = json.loads(response)
        
        if response_data.get('status') != 'success':
            raise Exception(f"Handshake failed: {response_data.get('error', 'Unknown error')}")
        
        # Step 5: Unwrap (decrypt) AES key using our private key
        wrapped_aes_key_b64 = response_data.get('wrapped_aes_key')
        if not wrapped_aes_key_b64:
            raise Exception("No wrapped AES key received from tunnel")
        
        wrapped_aes_key = base64.b64decode(wrapped_aes_key_b64)
        
        logger.info("🔓 Unwrapping AES key with RSA private key...")
        self.aes_key = self.rsa_private_key.decrypt(
            wrapped_aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        
        auth_method = response_data.get('auth_method', 'Unknown')
        
        logger.info(f"✅ Secure handshake complete via {auth_method}")
        logger.info(f"🔒 AES-256 key established via RSA-2048 key exchange")
    
    async def handle_messages(self):
        """Handle incoming messages from Snowflake"""
        try:
            async for encrypted_message in self.websocket:
                await self.handle_message(encrypted_message)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("⚠️  Connection closed")
            self.connected = False
        except Exception as e:
            logger.error(f"❌ Error handling messages: {e}")
            self.connected = False
    
    async def push_port_mappings(self):
        """Push port mapping configuration to tunnel sidecar"""
        try:
            message = {
                'type': 'port_mapping',
                'mappings': self.port_mappings
            }
            
            encrypted = self.encrypt_message(json.dumps(message))
            await self.websocket.send(encrypted)
            
            logger.info(f"📤 Pushed {len(self.port_mappings)} port mappings to tunnel")
            
        except Exception as e:
            logger.error(f"❌ Failed to push port mappings: {e}")
    
    async def handle_message(self, encrypted_message):
        """Handle a single encrypted message"""
        try:
            # Decrypt message
            decrypted = self.decrypt_message(encrypted_message)
            message = json.loads(decrypted)
            
            msg_type = message.get('type')
            logger.info(f"📥 Received message type: {msg_type}")
            
            if msg_type == 'session_create':
                # Create a new persistent session
                await self.handle_session_create(message)
            elif msg_type == 'session_data':
                # Data for an existing session
                await self.handle_session_data(message)
            elif msg_type == 'session_close':
                # Close a session
                await self.handle_session_close(message)
            elif msg_type == 'request_port_mappings':
                # Tunnel sidecar requesting port mappings (e.g., after Snowflake restart)
                logger.info("📥 Tunnel requesting port mappings (likely after Snowflake restart)")
                await self.push_port_mappings()
            elif msg_type == 'forward_data':
                # Port forwarding request from tunnel sidecar (legacy)
                await self.handle_forward_data(message)
            elif msg_type == 'tcp_forward':
                # Legacy: Forward TCP data to target
                await self.handle_tcp_forward(message)
            elif msg_type == 'query':
                # Legacy: Query forwarding mode (for backward compatibility)
                await self.handle_query(message)
            else:
                logger.warning(f"⚠️  Unknown message type: {msg_type}")
                
        except Exception as e:
            logger.error(f"❌ Error handling message: {e}")
    
    async def handle_session_create(self, message):
        """Create a new persistent session to on-premise service"""
        session_id = message.get('session_id')
        remote_host = message.get('remote_host')
        remote_port = message.get('remote_port')
        
        try:
            logger.info(f"📨 Creating session {session_id} to {remote_host}:{remote_port}")
            
            # ALWAYS create fresh connection (PostgreSQL is stateful, can't pool raw TCP)
            logger.info(f"🔌 Creating new connection to {remote_host}:{remote_port}")
            reader, writer = await asyncio.open_connection(remote_host, remote_port)
            
            # Start task to forward data from on-premise to tunnel
            task = asyncio.create_task(
                self._forward_from_onpremise(session_id, reader)
            )
            
            # Store session
            self.sessions[session_id] = {
                'reader': reader,
                'writer': writer,
                'task': task,
                'remote_host': remote_host,
                'remote_port': remote_port
            }
            
            # Send acknowledgment
            response = {
                'type': 'session_create_response',
                'session_id': session_id,
                'success': True
            }
            
            encrypted = self.encrypt_message(json.dumps(response))
            await self.websocket.send(encrypted)
            
            logger.info(f"✅ Session {session_id} created successfully")
            
            # Process any buffered data that arrived before session was created
            if session_id in self.pending_session_data:
                buffered_messages = self.pending_session_data.pop(session_id)
                logger.info(f"📦 Processing {len(buffered_messages)} buffered messages for {session_id}")
                for buffered_msg in buffered_messages:
                    await self.handle_session_data(buffered_msg)
            
        except Exception as e:
            logger.error(f"❌ Failed to create session {session_id}: {e}")
            
            # Send error response
            response = {
                'type': 'session_create_response',
                'session_id': session_id,
                'success': False,
                'error': str(e)
            }
            
            encrypted = self.encrypt_message(json.dumps(response))
            await self.websocket.send(encrypted)
    
    async def handle_session_data(self, message):
        """Forward data from tunnel to on-premise service"""
        session_id = message.get('session_id')
        data_hex = message.get('data')
        
        if session_id not in self.sessions:
            # Buffer the message - session_create might be arriving soon
            if session_id not in self.pending_session_data:
                self.pending_session_data[session_id] = []
                logger.info(f"⏳ Buffering data for not-yet-created session {session_id}")
            
            self.pending_session_data[session_id].append(message)
            
            # If we've buffered too many messages (>5), it's a real problem
            if len(self.pending_session_data[session_id]) > 5:
                logger.warning(f"⚠️  Session {session_id} still not found after {len(self.pending_session_data[session_id])} messages - requesting reset")
                
                # Clear buffer and request reset
                self.pending_session_data.pop(session_id, None)
                
                try:
                    reset_msg = {
                        'type': 'session_reset',
                        'session_id': session_id,
                        'reason': 'Session not found on agent (reconnect or restart)'
                    }
                    encrypted = self.encrypt_message(json.dumps(reset_msg))
                    await self.websocket.send(encrypted)
                    logger.info(f"📤 Sent session_reset for {session_id}")
                except Exception as e:
                    logger.error(f"❌ Failed to send session_reset: {e}")
            return
        
        try:
            data = bytes.fromhex(data_hex) if data_hex else b''
            
            session = self.sessions[session_id]
            writer = session['writer']
            
            # Forward data to on-premise service
            writer.write(data)
            await writer.drain()
            
        except Exception as e:
            logger.error(f"❌ Error forwarding data for session {session_id}: {e}")
            await self.handle_session_close({'session_id': session_id})
    
    async def handle_session_close(self, message):
        """Close a session and return connection to pool"""
        session_id = message.get('session_id')
        
        # Clean up any pending buffered data
        if session_id in self.pending_session_data:
            self.pending_session_data.pop(session_id)
            logger.debug(f"🧹 Cleared buffered data for closing session {session_id}")
        
        if session_id not in self.sessions:
            logger.debug(f"⚠️  Session {session_id} not found for closing")
            return
        
        try:
            session = self.sessions.pop(session_id)
            
            # Cancel forwarding task
            session['task'].cancel()
            try:
                await session['task']
            except asyncio.CancelledError:
                pass
            
            # Close the connection (PostgreSQL connections can't be pooled at TCP level)
            writer = session['writer']
            writer.close()
            await writer.wait_closed()
            
            logger.info(f"🔌 Session {session_id} closed")
            
        except Exception as e:
            logger.error(f"❌ Error closing session {session_id}: {e}")
    
    async def _forward_from_onpremise(self, session_id: str, reader):
        """Forward data from on-premise service back to tunnel"""
        try:
            while True:
                data = await reader.read(8192)
                if not data:
                    break
                
                # Send data back to tunnel
                response = {
                    'type': 'session_data_response',
                    'session_id': session_id,
                    'data': data.hex()
                }
                
                encrypted = self.encrypt_message(json.dumps(response))
                await self.websocket.send(encrypted)
                
        except Exception as e:
            logger.debug(f"Session {session_id} read ended: {e}")
        finally:
            # Notify tunnel that session is closed
            if session_id in self.sessions:
                await self.handle_session_close({'session_id': session_id})
    
    async def handle_forward_data(self, message):
        """
        Handle port forwarding request from tunnel sidecar
        Forwards data to the actual on-premise service
        """
        try:
            request_id = message.get('request_id')
            remote_host = message.get('remote_host')
            remote_port = message.get('remote_port')
            data_hex = message.get('data')
            
            logger.debug(f"📨 Forward request {request_id} to {remote_host}:{remote_port}")
            
            # Decode data
            data = bytes.fromhex(data_hex) if data_hex else b''
            
            # Connect to on-premise service
            reader, writer = await asyncio.open_connection(remote_host, remote_port)
            
            # Send data
            writer.write(data)
            await writer.drain()
            
            # Read response
            try:
                response_data = await asyncio.wait_for(reader.read(8192), timeout=30)
            except asyncio.TimeoutError:
                response_data = b''
            
            # Close connection
            writer.close()
            await writer.wait_closed()
            
            # Send response back to tunnel
            response_msg = {
                'type': 'forward_response',
                'request_id': request_id,
                'data': response_data.hex()
            }
            
            encrypted_response = self.encrypt_message(json.dumps(response_msg))
            await self.websocket.send(encrypted_response)
            
            logger.debug(f"✅ Forward complete {request_id}: {len(response_data)} bytes")
            
        except Exception as e:
            logger.error(f"❌ Forward error: {e}")
            # Send error response
            error_msg = {
                'type': 'forward_response',
                'request_id': message.get('request_id'),
                'error': str(e),
                'data': ''
            }
            encrypted_error = self.encrypt_message(json.dumps(error_msg))
            await self.websocket.send(encrypted_error)
    
    async def handle_query(self, message):
        """
        Handle query forwarding with dynamic destination routing
        Destination can be specified per-query or use configured defaults
        """
        try:
            import asyncpg
            
            request_id = message.get('request_id')
            query = message.get('query')
            params = message.get('params', [])
            
            # Get target from message (required - Snowflake agent always specifies)
            target_host = message.get('target_host')
            target_port = message.get('target_port')
            
            if not target_host or not target_port:
                raise Exception("No target specified - target_host and target_port are required")
            
            logger.info(f"📨 Query request {request_id} to {target_host}:{target_port}: {query[:100]}...")
            
            # Connect to database and execute
            conn = await asyncpg.connect(
                host=target_host,
                port=target_port,
                database=self.db_name,
                user=self.db_user,
                password=self.db_password if self.db_password else None
            )
            
            try:
                # Execute query
                if query.strip().upper().startswith(('SELECT', 'SHOW', 'DESC')):
                    # SELECT query - fetch results
                    rows = await conn.fetch(query, *params)
                    
                    # Convert rows to dicts and handle datetime serialization
                    serialized_rows = []
                    for row in rows:
                        row_dict = {}
                        for key, value in dict(row).items():
                            # Convert datetime objects to ISO format strings
                            if hasattr(value, 'isoformat'):
                                row_dict[key] = value.isoformat()
                            else:
                                row_dict[key] = value
                        serialized_rows.append(row_dict)
                    
                    result = {
                        'type': 'query_response',
                        'request_id': request_id,
                        'success': True,
                        'rows': serialized_rows,
                        'rowcount': len(rows)
                    }
                else:
                    # DML/DDL query
                    status = await conn.execute(query, *params)
                    result = {
                        'type': 'query_response',
                        'request_id': request_id,
                        'success': True,
                        'rows': [],
                        'rowcount': 0,
                        'status': status
                    }
                
                logger.info(f"✅ Query executed successfully")
                
            finally:
                await conn.close()
            
            # Send response
            encrypted_response = self.encrypt_message(json.dumps(result))
            await self.websocket.send(encrypted_response)
            
        except Exception as e:
            logger.error(f"❌ Query execution error: {e}")
            error_result = {
                'type': 'query_response',
                'request_id': message.get('request_id'),
                'success': False,
                'error': str(e),
                'rows': []
            }
            encrypted_error = self.encrypt_message(json.dumps(error_result))
            await self.websocket.send(encrypted_error)
    
    async def handle_tcp_forward(self, message):
        """Handle TCP forwarding request"""
        try:
            request_id = message.get('request_id')
            data_hex = message.get('data')
            
            if not data_hex:
                logger.warning(f"⚠️  No data in TCP forward request {request_id}")
                return
            
            # Decode hex data
            data = bytes.fromhex(data_hex)
            
            logger.debug(f"📨 TCP forward request {request_id}: {len(data)} bytes")
            
            # Forward to target
            reader, writer = await asyncio.open_connection(
                self.target_host,
                self.target_port
            )
            
            # Send data
            writer.write(data)
            await writer.drain()
            
            # Read response (with timeout)
            try:
                response_data = await asyncio.wait_for(
                    reader.read(65536),  # Read up to 64KB
                    timeout=30
                )
            except asyncio.TimeoutError:
                response_data = b''
            
            # Close connection
            writer.close()
            await writer.wait_closed()
            
            # Send response back through tunnel
            response_msg = {
                'type': 'tcp_response',
                'request_id': request_id,
                'data': response_data.hex()
            }
            
            encrypted_response = self.encrypt_message(json.dumps(response_msg))
            await self.websocket.send(encrypted_response)
            
            logger.debug(f"✅ TCP forward complete {request_id}: {len(response_data)} bytes")
            
        except Exception as e:
            logger.error(f"❌ TCP forward error: {e}")
            # Send error response
            error_msg = {
                'type': 'tcp_response',
                'request_id': message.get('request_id'),
                'error': str(e),
                'data': ''
            }
            encrypted_error = self.encrypt_message(json.dumps(error_msg))
            await self.websocket.send(encrypted_error)
    
    def encrypt_message(self, message: str) -> bytes:
        """Encrypt message with AES-256"""
        import secrets
        iv = secrets.token_bytes(16)
        cipher = Cipher(
            algorithms.AES(self.aes_key),
            modes.CFB(iv),
            backend=default_backend()
        )
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(message.encode()) + encryptor.finalize()
        return iv + ciphertext
    
    def decrypt_message(self, encrypted: bytes) -> str:
        """Decrypt AES-256 message"""
        iv = encrypted[:16]
        ciphertext = encrypted[16:]
        cipher = Cipher(
            algorithms.AES(self.aes_key),
            modes.CFB(iv),
            backend=default_backend()
        )
        decryptor = cipher.decryptor()
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        return plaintext.decode()


async def main():
    agent = OnPremiseAgent()
    await agent.start()


if __name__ == "__main__":
    asyncio.run(main())
