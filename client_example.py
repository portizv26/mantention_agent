"""
Example client for the Maintenance Chatbot API
Demonstrates how to interact with the FastAPI microservice
"""

import asyncio
import json
import time
from typing import Dict, Optional
from pathlib import Path

import httpx
import streamlit as st

class ChatbotClient:
    """Client for interacting with the Maintenance Chatbot API"""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session_id: Optional[str] = None
        
    async def send_message(self, message: str) -> Dict:
        """Send a message to the chatbot and return the response"""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/chat",
                json={
                    "message": message,
                    "session_id": self.session_id
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                data = response.json()
                self.session_id = data["session_id"]  # Store session ID
                return data
            else:
                response.raise_for_status()
    
    async def download_artifact(self, file_type: str, filename: str = None) -> bytes:
        """Download an artifact from the chatbot"""
        if not self.session_id:
            raise ValueError("No active session")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/v1/download/{file_type}/{self.session_id}",
                timeout=30.0
            )
            
            if response.status_code == 200:
                return response.content
            else:
                response.raise_for_status()
    
    async def get_session_artifacts(self) -> Dict:
        """Get list of available artifacts for current session"""
        if not self.session_id:
            return {"artifacts": {}}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/v1/sessions/{self.session_id}/artifacts",
                timeout=10.0
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                return {"artifacts": {}}
    
    async def health_check(self) -> Dict:
        """Check API health"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/health",
                timeout=5.0
            )
            return response.json()

# ─────────────────────────── STREAMLIT APP EXAMPLE ─────────────────────────── #

def create_streamlit_app():
    """Example Streamlit app using the new API"""
    st.set_page_config(
        page_title="Maintenance Chatbot",
        page_icon="🔧",
        layout="wide"
    )
    
    st.title("🔧 Maintenance Chatbot - API Version")
    
    # Initialize client
    if "client" not in st.session_state:
        st.session_state.client = ChatbotClient()
    
    if "messages" not in st.session_state:
        st.session_state.messages = []
    
    # Sidebar with API status
    with st.sidebar:
        st.header("📊 API Status")
        
        if st.button("Check Health"):
            try:
                health = asyncio.run(st.session_state.client.health_check())
                st.success(f"Status: {health['status']}")
                st.json(health)
            except Exception as e:
                st.error(f"API unavailable: {e}")
        
        st.header("📁 Session Artifacts")
        try:
            artifacts = asyncio.run(st.session_state.client.get_session_artifacts())
            if artifacts.get("artifacts"):
                for artifact_type, info in artifacts["artifacts"].items():
                    st.write(f"**{artifact_type.title()}**: {info['type']}")
                    if st.button(f"Download {artifact_type}", key=f"download_{artifact_type}"):
                        try:
                            content = asyncio.run(
                                st.session_state.client.download_artifact(artifact_type)
                            )
                            st.download_button(
                                f"💾 Save {artifact_type}",
                                content,
                                file_name=f"{artifact_type}.{info['type']}",
                                key=f"save_{artifact_type}"
                            )
                        except Exception as e:
                            st.error(f"Download failed: {e}")
            else:
                st.info("No artifacts available yet")
        except Exception as e:
            st.error(f"Cannot load artifacts: {e}")
    
    # Chat interface
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])
            if "metrics" in message:
                with st.expander("📈 Performance Metrics"):
                    st.json(message["metrics"])
    
    # Chat input
    if prompt := st.chat_input("Escribe tu pregunta sobre mantenimiento..."):
        # Add user message
        st.session_state.messages.append({"role": "user", "content": prompt})
        
        with st.chat_message("user"):
            st.write(prompt)
        
        # Get bot response
        with st.chat_message("assistant"):
            with st.spinner("Procesando..."):
                try:
                    response_data = asyncio.run(
                        st.session_state.client.send_message(prompt)
                    )
                    
                    st.write(response_data["response"])
                    
                    # Show metrics
                    metrics = response_data.get("metrics", {})
                    if metrics:
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("Tiempo Total", f"{metrics.get('total_time', 0):.2f}s")
                        with col2:
                            st.metric("Tiempo SQL", f"{metrics.get('sql_time', 0):.2f}s")
                        with col3:
                            st.metric("Intentos SQL", metrics.get('sql_attempts', 0))
                    
                    # Add assistant message
                    st.session_state.messages.append({
                        "role": "assistant", 
                        "content": response_data["response"],
                        "metrics": metrics
                    })
                    
                    # Show artifacts if available
                    if response_data.get("artifacts"):
                        st.success("✅ Nuevos archivos generados - revisa la barra lateral")
                    
                except Exception as e:
                    error_msg = f"Error: {e}"
                    st.error(error_msg)
                    st.session_state.messages.append({
                        "role": "assistant", 
                        "content": error_msg
                    })

# ─────────────────────────── COMMAND LINE EXAMPLE ─────────────────────────── #

async def command_line_example():
    """Example of using the client from command line"""
    client = ChatbotClient()
    
    print("🔧 Maintenance Chatbot CLI")
    print("Type 'quit' to exit\n")
    
    # Check API health
    try:
        health = await client.health_check()
        print(f"✅ API Status: {health['status']}")
    except Exception as e:
        print(f"❌ API Error: {e}")
        return
    
    while True:
        user_input = input("\n👤 You: ").strip()
        
        if user_input.lower() in ['quit', 'exit', 'q']:
            break
        
        if not user_input:
            continue
        
        try:
            print("🤖 Thinking...")
            start_time = time.time()
            
            response_data = await client.send_message(user_input)
            
            end_time = time.time()
            
            print(f"\n🤖 Bot: {response_data['response']}")
            
            # Show metrics
            metrics = response_data.get('metrics', {})
            print(f"\n📊 Metrics:")
            print(f"   • Total time: {metrics.get('total_time', 0):.2f}s")
            print(f"   • API time: {end_time - start_time:.2f}s")
            print(f"   • Flow: {metrics.get('flow', 'unknown')}")
            
            # Show artifacts
            artifacts = response_data.get('artifacts')
            if artifacts:
                print(f"\n📁 Artifacts generated:")
                for key, path in artifacts.items():
                    print(f"   • {key}: {path}")
                
                # Ask if user wants to download
                download = input("\nDownload artifacts? (y/n): ").lower().startswith('y')
                if download:
                    for artifact_type in artifacts.keys():
                        try:
                            content = await client.download_artifact(artifact_type)
                            filename = f"download_{artifact_type}.{'csv' if 'data' in artifact_type else 'png' if 'image' in artifact_type else 'py'}"
                            Path(filename).write_bytes(content)
                            print(f"   ✅ Downloaded {filename}")
                        except Exception as e:
                            print(f"   ❌ Failed to download {artifact_type}: {e}")
            
        except Exception as e:
            print(f"\n❌ Error: {e}")

# ─────────────────────────── BATCH PROCESSING EXAMPLE ─────────────────────────── #

async def batch_processing_example():
    """Example of batch processing multiple queries"""
    client = ChatbotClient()
    
    queries = [
        "¿Cuántos equipos hay en total?",
        "¿Cuáles equipos necesitan mantenimiento?",
        "Muestra un gráfico del estado de los equipos",
        "¿Cuándo fue el último mantenimiento de cada equipo?"
    ]
    
    print("🔄 Processing batch queries...\n")
    
    results = []
    
    for i, query in enumerate(queries, 1):
        print(f"[{i}/{len(queries)}] Processing: {query}")
        
        try:
            response = await client.send_message(query)
            results.append({
                "query": query,
                "response": response["response"],
                "metrics": response["metrics"],
                "success": True
            })
            print(f"   ✅ Completed in {response['metrics'].get('total_time', 0):.2f}s")
            
        except Exception as e:
            results.append({
                "query": query,
                "error": str(e),
                "success": False
            })
            print(f"   ❌ Failed: {e}")
        
        # Small delay between requests
        await asyncio.sleep(1)
    
    # Summary
    print(f"\n📊 Batch Processing Summary:")
    successful = sum(1 for r in results if r["success"])
    print(f"   • Successful: {successful}/{len(queries)}")
    
    total_time = sum(r["metrics"].get("total_time", 0) for r in results if r["success"])
    print(f"   • Total processing time: {total_time:.2f}s")
    
    avg_time = total_time / successful if successful > 0 else 0
    print(f"   • Average time per query: {avg_time:.2f}s")
    
    return results

# ─────────────────────────── MAIN ─────────────────────────── #

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "streamlit":
            # Run Streamlit app
            create_streamlit_app()
        elif sys.argv[1] == "batch":
            # Run batch processing
            asyncio.run(batch_processing_example())
        else:
            print("Usage: python client_example.py [streamlit|batch]")
    else:
        # Run CLI by default
        asyncio.run(command_line_example())
