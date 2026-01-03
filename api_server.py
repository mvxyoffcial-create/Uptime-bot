from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, HttpUrl
from typing import Optional, List
from datetime import datetime
import os
from bson import ObjectId
import asyncio
import aiohttp
import time

# Configuration
MONGODB_URI = os.getenv('MONGODB_URI')

# MongoDB setup
client = AsyncIOMotorClient(MONGODB_URI)
db = client['uptime_bot']
websites_collection = db['websites']
users_collection = db['users']
api_keys_collection = db['api_keys']

app = FastAPI(
    title="Uptime Monitor API",
    description="REST API for uptime monitoring",
    version="1.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Models
class AddWebsiteRequest(BaseModel):
    url: HttpUrl
    interval: str = "5min"

class UpdateWebsiteRequest(BaseModel):
    interval: Optional[str] = None
    notifications_enabled: Optional[bool] = None

class APIResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    message: str

# Authentication
async def verify_api_key(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    
    api_key = authorization.replace("Bearer ", "")
    
    key_doc = await api_keys_collection.find_one({'api_key': api_key, 'active': True})
    
    if not key_doc:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    
    # Update last used and request count
    await api_keys_collection.update_one(
        {'_id': key_doc['_id']},
        {
            '$set': {'last_used': datetime.utcnow()},
            '$inc': {'requests_count': 1}
        }
    )
    
    return key_doc['user_id']

async def check_website(url: str):
    """Check if website is up"""
    start_time = time.time()
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(str(url), allow_redirects=True) as response:
                response_time = int((time.time() - start_time) * 1000)
                return {
                    'status': 'up' if response.status < 500 else 'down',
                    'status_code': response.status,
                    'response_time': response_time,
                    'checked_at': datetime.utcnow(),
                    'error': None
                }
    except Exception as e:
        return {
            'status': 'down',
            'status_code': 0,
            'response_time': int((time.time() - start_time) * 1000),
            'checked_at': datetime.utcnow(),
            'error': str(e)
        }

# Endpoints
@app.get("/")
async def root():
    return {
        "name": "Uptime Monitor API",
        "version": "1.0.0",
        "docs": "/docs"
    }

@app.get("/api/v1/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@app.post("/api/v1/websites", response_model=APIResponse)
async def add_website(
    request: AddWebsiteRequest,
    user_id: int = Depends(verify_api_key)
):
    """Add a new website to monitor"""
    
    # Validate interval
    valid_intervals = ['10sec', '30sec', '1min', '2min', '3min', '5min', '10min', '15min', '30min', '1hour']
    if request.interval not in valid_intervals:
        raise HTTPException(status_code=400, detail=f"Invalid interval. Must be one of: {', '.join(valid_intervals)}")
    
    # Check if website already exists
    existing = await websites_collection.find_one({
        'user_id': user_id,
        'url': str(request.url)
    })
    
    if existing:
        raise HTTPException(status_code=400, detail="Website already exists")
    
    # Initial check
    check_result = await check_website(str(request.url))
    
    # Map interval to seconds
    interval_map = {
        '10sec': 10, '30sec': 30, '1min': 60, '2min': 120,
        '3min': 180, '5min': 300, '10min': 600, '15min': 900,
        '30min': 1800, '1hour': 3600
    }
    
    website_data = {
        'user_id': user_id,
        'url': str(request.url),
        'interval': request.interval,
        'interval_seconds': interval_map[request.interval],
        'added_at': datetime.utcnow(),
        'status': check_result['status'],
        'last_checked': check_result['checked_at'],
        'last_status_code': check_result['status_code'],
        'last_response_time': check_result['response_time'],
        'uptime_percentage': 100.0,
        'total_checks': 1,
        'successful_checks': 1 if check_result['status'] == 'up' else 0,
        'notifications_enabled': True
    }
    
    result = await websites_collection.insert_one(website_data)
    website_data['_id'] = str(result.inserted_id)
    
    return APIResponse(
        success=True,
        data=website_data,
        message="Website added successfully"
    )

@app.get("/api/v1/websites", response_model=APIResponse)
async def list_websites(user_id: int = Depends(verify_api_key)):
    """Get all websites for the authenticated user"""
    
    websites = await websites_collection.find({'user_id': user_id}).to_list(None)
    
    for website in websites:
        website['_id'] = str(website['_id'])
        website['added_at'] = website['added_at'].isoformat()
        website['last_checked'] = website['last_checked'].isoformat()
    
    return APIResponse(
        success=True,
        data={'websites': websites, 'count': len(websites)},
        message="Websites retrieved successfully"
    )

@app.get("/api/v1/websites/{website_id}", response_model=APIResponse)
async def get_website(
    website_id: str,
    user_id: int = Depends(verify_api_key)
):
    """Get specific website details"""
    
    try:
        website = await websites_collection.find_one({
            '_id': ObjectId(website_id),
            'user_id': user_id
        })
    except:
        raise HTTPException(status_code=400, detail="Invalid website ID")
    
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    
    website['_id'] = str(website['_id'])
    website['added_at'] = website['added_at'].isoformat()
    website['last_checked'] = website['last_checked'].isoformat()
    
    return APIResponse(
        success=True,
        data=website,
        message="Website retrieved successfully"
    )

@app.put("/api/v1/websites/{website_id}", response_model=APIResponse)
async def update_website(
    website_id: str,
    request: UpdateWebsiteRequest,
    user_id: int = Depends(verify_api_key)
):
    """Update website settings"""
    
    update_data = {}
    
    if request.interval:
        valid_intervals = ['10sec', '30sec', '1min', '2min', '3min', '5min', '10min', '15min', '30min', '1hour']
        if request.interval not in valid_intervals:
            raise HTTPException(status_code=400, detail="Invalid interval")
        
        interval_map = {
            '10sec': 10, '30sec': 30, '1min': 60, '2min': 120,
            '3min': 180, '5min': 300, '10min': 600, '15min': 900,
            '30min': 1800, '1hour': 3600
        }
        
        update_data['interval'] = request.interval
        update_data['interval_seconds'] = interval_map[request.interval]
    
    if request.notifications_enabled is not None:
        update_data['notifications_enabled'] = request.notifications_enabled
    
    if not update_data:
        raise HTTPException(status_code=400, detail="No update data provided")
    
    try:
        result = await websites_collection.update_one(
            {'_id': ObjectId(website_id), 'user_id': user_id},
            {'$set': update_data}
        )
    except:
        raise HTTPException(status_code=400, detail="Invalid website ID")
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Website not found")
    
    return APIResponse(
        success=True,
        data=update_data,
        message="Website updated successfully"
    )

@app.delete("/api/v1/websites/{website_id}", response_model=APIResponse)
async def delete_website(
    website_id: str,
    user_id: int = Depends(verify_api_key)
):
    """Delete a website"""
    
    try:
        result = await websites_collection.delete_one({
            '_id': ObjectId(website_id),
            'user_id': user_id
        })
    except:
        raise HTTPException(status_code=400, detail="Invalid website ID")
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Website not found")
    
    return APIResponse(
        success=True,
        data={'website_id': website_id},
        message="Website deleted successfully"
    )

@app.get("/api/v1/stats", response_model=APIResponse)
async def get_statistics(user_id: int = Depends(verify_api_key)):
    """Get user statistics"""
    
    websites = await websites_collection.find({'user_id': user_id}).to_list(None)
    
    if not websites:
        return APIResponse(
            success=True,
            data={
                'total_websites': 0,
                'online_websites': 0,
                'offline_websites': 0,
                'average_uptime': 0,
                'average_response_time': 0
            },
            message="No websites found"
        )
    
    total_sites = len(websites)
    online_sites = sum(1 for site in websites if site['status'] == 'up')
    offline_sites = total_sites - online_sites
    
    avg_uptime = sum(site.get('uptime_percentage', 100) for site in websites) / total_sites
    avg_response = sum(site.get('last_response_time', 0) for site in websites) / total_sites
    
    stats = {
        'total_websites': total_sites,
        'online_websites': online_sites,
        'offline_websites': offline_sites,
        'average_uptime': round(avg_uptime, 2),
        'average_response_time': round(avg_response, 2),
        'generated_at': datetime.utcnow().isoformat()
    }
    
    return APIResponse(
        success=True,
        data=stats,
        message="Statistics retrieved successfully"
    )

@app.post("/api/v1/websites/{website_id}/check", response_model=APIResponse)
async def manual_check(
    website_id: str,
    user_id: int = Depends(verify_api_key)
):
    """Manually trigger a website check"""
    
    try:
        website = await websites_collection.find_one({
            '_id': ObjectId(website_id),
            'user_id': user_id
        })
    except:
        raise HTTPException(status_code=400, detail="Invalid website ID")
    
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    
    # Perform check
    check_result = await check_website(website['url'])
    
    # Update database
    total_checks = website.get('total_checks', 0) + 1
    successful_checks = website.get('successful_checks', 0)
    
    if check_result['status'] == 'up':
        successful_checks += 1
    
    uptime_percentage = (successful_checks / total_checks) * 100
    
    await websites_collection.update_one(
        {'_id': ObjectId(website_id)},
        {
            '$set': {
                'status': check_result['status'],
                'last_checked': check_result['checked_at'],
                'last_status_code': check_result['status_code'],
                'last_response_time': check_result['response_time'],
                'uptime_percentage': uptime_percentage,
                'total_checks': total_checks,
                'successful_checks': successful_checks
            }
        }
    )
    
    return APIResponse(
        success=True,
        data=check_result,
        message="Website checked successfully"
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
