from django.shortcuts import render, redirect, get_object_or_404
from django.db import connection
from django.http import HttpResponse
from django.core.files.storage import FileSystemStorage
from django.contrib.auth.hashers import make_password
from django.contrib.auth.hashers import check_password
from django.contrib.auth import logout
import boto3
from django.conf import settings
import os
from django.http import JsonResponse
import requests
import uuid
import replicate
from dotenv import load_dotenv
import stripe
from django.utils import timezone
from datetime import datetime
from django.contrib import messages
from .models import SupportThread, ThreadMessage
from django.core.paginator import Paginator
from django.db.models import Q
from typing import Any, Optional

def homepage(request):
    username = request.session.get('username')
    profile_picture_url = '/static/images/dpfp.png'
    is_premium = False
    if username:
        # Use the URL name instead of hardcoding the path
        profile_picture_url = f'/profile-picture/{username}'  # Note the trailing slash
        # Prefer session cache, fall back to DB
        is_premium = bool(request.session.get('is_premium', False))
        if not is_premium:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT premium FROM wallify_users WHERE username = %s", [username])
                    row = cursor.fetchone()
                    if row is not None:
                        is_premium = bool(row[0])
                        request.session['is_premium'] = is_premium
            except Exception:
                # If the column doesn't exist or query fails, fall back to non-premium
                is_premium = False

    # If returned from Stripe checkout with a session_id, verify and upgrade without waiting for webhook
    session_id = request.GET.get('session_id')
    if request.GET.get('checkout') == 'success' and session_id:
        try:
            load_dotenv()
            stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
            sess = stripe.checkout.Session.retrieve(session_id)
            # Accept paid/complete subscription sessions
            mode_ok = (sess.get('mode') == 'subscription')
            paid_ok = (sess.get('payment_status') == 'paid') or (sess.get('status') in ('complete', 'completed'))
            # Identify username via metadata or client_reference_id
            uname = None
            md = sess.get('metadata') or {}
            if isinstance(md, dict):
                uname = md.get('username')
            if not uname:
                uname = sess.get('client_reference_id')
            if mode_ok and paid_ok and uname:
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE wallify_users SET premium = 1 WHERE username = %s", [uname])
                if request.session.get('username') == uname:
                    request.session['is_premium'] = True
                # Redirect to clean URL
                return redirect('homepage')
        except Exception as e:
            # Best-effort; ignore errors here and just render normally
            print('Checkout verification failed:', str(e))

    return render(request, 'homepage.html', {
        'username': username,
        'profile_picture_url': profile_picture_url,
        'is_premium': is_premium,
    })

def about(request):
    username = request.session.get('username')
    profile_picture_url = '/static/images/dpfp.png'
    if username:
        # Use the URL name instead of hardcoding the path
        profile_picture_url = f'/profile-picture/{username}'

    return render(request, 'about.html', {'username': username, 'profile_picture_url': profile_picture_url})

def image_generation(request):
    return render(request, 'generate_image.html')

def gallery(request):
    query = request.GET.get('q', '4k wallpapers')  # Default to 'free wallpapers' if no query is provided
    s3_image_urls = []
    top_search_terms = []

    # Split the search query into individual words
    keywords = query.split()

    # Construct a SQL query dynamically to search for all keywords in the description
    conditions = []
    query_params = []
    for keyword in keywords:
        conditions.append("description ILIKE %s")
        query_params.append('%' + keyword + '%')

    # Update SQL query to include username
    sql_query = "SELECT image_key, \"user\" FROM images_table WHERE " + " AND ".join(conditions)

    # Execute the SQL query
    with connection.cursor() as cursor:
        cursor.execute(sql_query, query_params)
        results = cursor.fetchall()

    # S3 bucket URL with '/images/' path
    s3_bucket_url = 'https://{}.s3.amazonaws.com/images/'.format(settings.AWS_STORAGE_BUCKET_NAME)

    # Generate image URLs and include usernames
    for result in results:
        image_key, username = result
        image_url = s3_bucket_url + image_key
        s3_image_urls.append({'url': image_url, 'key': image_key, 'username': username})

    # Log the search term into the search_logs table
    with connection.cursor() as cursor:
        cursor.execute("SELECT search_count FROM search_logs WHERE search_term = %s", [query])
        row = cursor.fetchone()

        if row:
            # If it exists, update the count
            new_count = row[0] + 1
            cursor.execute("UPDATE search_logs SET search_count = %s WHERE search_term = %s", [new_count, query])
        else:
            # If it doesn't exist, insert a new record
            cursor.execute("INSERT INTO search_logs (search_term, search_count) VALUES (%s, %s)", [query, 1])

    # Fetch top 10 search terms
    with connection.cursor() as cursor:
        cursor.execute("SELECT search_term FROM search_logs ORDER BY search_count DESC LIMIT 10")
        top_search_terms = [row[0] for row in cursor.fetchall()]

    # Get the username from session
    username = request.session.get('username')

    # Set a default profile picture URL
    profile_picture_url = '/static/images/dpfp.png'

    if username:
        profile_picture_url = f'/profile-picture/{username}'

    if request.method == 'POST':
        image_key = request.POST.get('image_key')
        if username:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO favorites (image_key, username) VALUES (%s, %s)", [image_key, username])

    # Render the gallery template with the search results and top search terms
    return render(request, 'gallery.html', {
        'results': s3_image_urls,
        'top_search_terms': top_search_terms,
        'username': username,
        'profile_picture_url': profile_picture_url
    })

# Authentication Views
def signin(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        with connection.cursor() as cursor:
            cursor.execute("SELECT username, password, premium FROM wallify_users WHERE username = %s", [username])
            user_data = cursor.fetchone()

        if user_data:
            # Extract hashed password from database
            hashed_password = user_data[1]
            premium_flag = user_data[2] if len(user_data) > 2 else 0

            # Check if the provided password matches the hashed password
            if check_password(password, hashed_password):
                request.session['username'] = username
                request.session['is_premium'] = bool(premium_flag)
                return JsonResponse({'success': True})
            else:
                return JsonResponse({'success': False, 'message': 'Invalid username or password'})
        else:
            return JsonResponse({'success': False, 'message': 'Invalid username or password'})

def signup(request):
    if request.method == 'POST':
        # Retrieve form data
        firstname = request.POST.get('firstname')
        lastname = request.POST.get('lastname')
        email = request.POST.get('email')
        username = request.POST.get('username')
        password1 = request.POST.get('password1')
        password2 = request.POST.get('password2')

        # Basic form validation
        if password1 != password2:
            return JsonResponse({'success': False, 'message': 'Passwords do not match'})

        try:
            # Hash the password
            hashed_password = make_password(password1)

            # Get the path to the static file
            pfp_path = os.path.join(settings.BASE_DIR, 'app', 'static', 'images', 'dpfp.png')

            # Read the default profile picture as binary data
            with open(pfp_path, 'rb') as pfp_file:
                pfp_blob = pfp_file.read()

            # Insert data into the database
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO wallify_users (firstname, lastname, email, username, password, pfp)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    [firstname, lastname, email, username, hashed_password, pfp_blob]
                )
            
            request.session['username'] = username
            request.session['is_premium'] = False
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})

    return JsonResponse({'success': False, 'message': 'Invalid request method'})

def logout_view(request):
    logout(request)  # Log out the user
    return redirect('homepage')  # Redirect to the homepage or any other page

#User PFP
def serve_profile_picture(request, user_id):
    with connection.cursor() as cursor:
        cursor.execute("SELECT pfp FROM wallify_users WHERE id = %s", [user_id])
        result = cursor.fetchone()

    if result:
        pfp_blob = result[0]
        if pfp_blob:
            return HttpResponse(pfp_blob, content_type='image/png')

    return HttpResponse(status=404)

def get_profile_picture(request, username):
    with connection.cursor() as cursor:
        cursor.execute("SELECT pfp FROM wallify_users WHERE username = %s", [username])
        row = cursor.fetchone()
        if row:
            pfp_data = row[0]
            response = HttpResponse(pfp_data, content_type='image/jpeg')  # Adjust content_type as needed
            return response
    return HttpResponse(status=404)

def profile(request, username=None):
    # Get the username of the logged-in user from the session
    logged_in_username = request.session.get('username')

    # Redirect to login if no username is in session
    if not logged_in_username:
        return redirect('signin')

    # If no username is provided, use the session username for the logged-in user's profile
    if username is None:
        username = logged_in_username

    # Set a default profile picture URL for the logged-in user
    profile_picture_url = '/static/images/default-profile-image2.png'
    if logged_in_username:
        profile_picture_url = f'/profile-picture/{logged_in_username}'

    # Handle profile picture update (only for the logged-in user)
    if request.method == 'POST' and logged_in_username == username:
        if 'newProfilePicture' in request.FILES:
            try:
                profile_picture = request.FILES['newProfilePicture']
                file_content = profile_picture.read()

                # Update the user's profile picture in the database
                with connection.cursor() as cursor:
                    cursor.execute("""
                        UPDATE wallify_users
                        SET pfp = %s
                        WHERE username = %s
                    """, [file_content, logged_in_username])

                # Redirect to the home page after update
                # If AJAX, return JSON so client can close modal without full redirect
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': True})
                return redirect('profile')

            except Exception as e:
                # Handle the error (e.g., display an error message)
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': False, 'message': 'Failed to update profile picture'}, status=500)
                return render(request, 'profile.html', {'error': 'Failed to update profile picture'})

    # Fetch user details and profile picture for the profile being viewed
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, firstname, lastname, email, username, pfp FROM wallify_users WHERE username = %s", [username])
        user = cursor.fetchone()

    if user:
        user_id, firstname, lastname, email, viewed_username, pfp_blob = user

        # Construct the URL for the profile picture of the viewed user
        pfp_url = None
        if pfp_blob:
            pfp_url = request.build_absolute_uri(f'/profile_picture/{user_id}/')

        # Fetch favorite images for the user whose profile is being viewed
        favorite_images = []
        with connection.cursor() as cursor:
            cursor.execute("SELECT image_key FROM favorites WHERE username = %s", [username])
            results = cursor.fetchall()
            for result in results:
                image_key = result[0]
                image_url = f'https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com/images/{image_key}'
                favorite_images.append({'url': image_url, 'key': image_key})

        # Fetch uploaded images for the logged-in user
        uploaded_images = []
        with connection.cursor() as cursor:
            cursor.execute("SELECT image_key FROM images_table WHERE \"user\" = %s", [username])
            results = cursor.fetchall()
            for result in results:
                image_key = result[0]
                image_url = f'https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com/images/{image_key}'
                uploaded_images.append({'url': image_url, 'key': image_key})

        context = {
            'firstname': firstname,
            'lastname': lastname,
            'email': email,
            'username': viewed_username,  # User whose profile is being viewed
            'pfp_url': pfp_url,
            'profile_picture_url': profile_picture_url,  # Profile picture URL for the logged-in user
            'favorite_images': favorite_images,
            'uploaded_images': uploaded_images,  # Images uploaded by the logged-in user
            'logged_in_username': logged_in_username,  # Added to display logged-in user's info in navbar
        }
    else:
        context = {'error': 'User not found'}

    return render(request, 'profile.html', context)

def upload_image(request):
    if request.method == 'POST':
        description = request.POST.get('description')
        
        # Get the uploaded image file
        uploaded_file = request.FILES.get('image')
        
        # Fetch the username from the session
        username = request.session.get('username')
        if not username:
            return HttpResponse("User not logged in", status=403)
        
        # Initialize a session using Amazon S3
        s3 = boto3.client('s3',
                          aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                          aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                          region_name=settings.AWS_S3_REGION_NAME)
        
        # Define the S3 bucket and file name
        bucket_name = settings.AWS_STORAGE_BUCKET_NAME
        file_name = uploaded_file.name
        file_content = uploaded_file.read()
        
        # Upload the file to S3
        s3.put_object(Bucket=bucket_name,
                      Key=f'images/{file_name}',
                      Body=file_content,
                      ContentType=uploaded_file.content_type)
        
        # Remove the 'images/' prefix for the database
        db_file_name = file_name

        # Save the image filename, description, and username to the database
        sql_query = """
            INSERT INTO images_table (image_key, description, "user")
            VALUES (%s, %s, %s)
        """
        with connection.cursor() as cursor:
            cursor.execute(sql_query, [db_file_name, description, username])
        
        return redirect('profile')
    else:
        return HttpResponse("Method not allowed", status=405)

def delete_favorite_image(request):
    username = request.session.get('username')
    image_key = request.POST.get('image_key')

    if not username:
        return JsonResponse({'error': 'User not authenticated'}, status=401)

    if not image_key:
        return JsonResponse({'error': 'No image key provided'}, status=400)

    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                DELETE FROM favorites 
                WHERE ctid = (
                    SELECT ctid 
                    FROM favorites 
                    WHERE username = %s AND image_key = %s 
                    LIMIT 1
                )
            """, [username, image_key])
            
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
    
def generate_image(request):
    username = request.session.get('username')
    profile_picture_url = '/static/images/dpfp.png'

    if username:
        profile_picture_url = f'/profile-picture/{username}'
    
    # Load environment variables from .env file
    load_dotenv()
    api_token = os.getenv('REPLICATE_API_TOKEN')
    
    if request.method == 'POST' and username:
        if not request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'error': 'Invalid request'}, status=400)

        prompt = request.POST.get('prompt')
        aspect_ratio = request.POST.get('aspect_ratio', '9:16')

        # Determine premium flag (0/1)
        is_premium = bool(request.session.get('is_premium', False))
        if username and not is_premium:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT premium FROM wallify_users WHERE username = %s", [username])
                    row = cursor.fetchone()
                    if row is not None:
                        is_premium = bool(row[0])
                        request.session['is_premium'] = is_premium
            except Exception:
                is_premium = False

        # Check daily limit (skip if premium)
        today = timezone.now().date()
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) 
                FROM ai_generations 
                WHERE username = %s 
                AND DATE(created_at) = %s
            """, [username, today])
            daily_count = cursor.fetchone()[0]

            if not is_premium and daily_count >= 3:
                return JsonResponse({
                    'error': 'Daily limit reached! You can generate up to 3 images per day.',
                    'upgrade': True
                }, status=403)

        try:
            output = replicate.run(
                # "stability-ai/sdxl:7762fd07cf82c948538e41f63f77d685e02b063e37e496e96eefd46c929f9bdc",
                "bytedance/seedream-3",
                # "bytedance/hyper-flux-16step:382cf8959fb0f0d665b26e7e80b8d6dc3faaef1510f14ce017e8c732bb3d1eb7",
                input={
                    "size": "regular",
                    "width": 2048,
                    "height": 2048,
                    "prompt": prompt,
                    "aspect_ratio": aspect_ratio,
                    "guidance_scale": 2.5
                    # "width": 1440,
                    # "height": 1440,
                    # "prompt": prompt,
                    # "num_outputs": 1,
                    # "aspect_ratio": aspect_ratio, 
                    # "output_format": "jpg",
                    # "guidance_scale": 3.5,
                    # "output_quality": 100,
                    # "num_inference_steps": 16,
                }
            )
            
            # Normalize various output shapes from Replicate into a single image URL
            def _extract_image_url(o: Any) -> Optional[str]:
                try:
                    # Case 1: direct string URL
                    if isinstance(o, str):
                        return o
                    # Case 2: list/tuple of URLs or dicts
                    if isinstance(o, (list, tuple)) and len(o) > 0:
                        first = o[0]
                        if isinstance(first, str):
                            return first
                        if isinstance(first, dict):
                            # common keys
                            for key in ("url", "image", "src"): 
                                if key in first and isinstance(first[key], str):
                                    return first[key]
                            # nested
                            if "images" in first and isinstance(first["images"], list) and first["images"]:
                                img0 = first["images"][0]
                                if isinstance(img0, str):
                                    return img0
                                if isinstance(img0, dict):
                                    for key in ("url", "image", "src"):
                                        if key in img0 and isinstance(img0[key], str):
                                            return img0[key]
                    # Case 3: dict output
                    if isinstance(o, dict):
                        # direct keys
                        for key in ("url", "image", "output"):
                            val = o.get(key)
                            if isinstance(val, str):
                                return val
                            if isinstance(val, (list, tuple)) and val:
                                if isinstance(val[0], str):
                                    return val[0]
                                if isinstance(val[0], dict):
                                    for k in ("url", "image", "src"):
                                        if k in val[0] and isinstance(val[0][k], str):
                                            return val[0][k]
                        # nested images
                        images = o.get("images")
                        if isinstance(images, (list, tuple)) and images:
                            if isinstance(images[0], str):
                                return images[0]
                            if isinstance(images[0], dict):
                                for k in ("url", "image", "src"):
                                    if k in images[0] and isinstance(images[0][k], str):
                                        return images[0][k]
                except Exception:
                    return None
                return None

            image_url = _extract_image_url(output)
            if not image_url:
                # Log unexpected shape for debugging
                print("Unexpected Replicate output shape:", type(output), output)
                return JsonResponse({'error': 'Failed to parse image URL from model output'}, status=500)

            # Record the generation in database
            with connection.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO ai_generations 
                    (username, prompt, image_url, aspect_ratio, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                """, [username, prompt, image_url, aspect_ratio, timezone.now()])

            # Download the image and mirror it into our S3 + DB like manual uploads
            try:
                r = requests.get(image_url, timeout=30)
                if not r.ok or not r.content:
                    raise RuntimeError(f"Failed to download generated image: status {r.status_code}")

                content = r.content
                # Infer content type and extension
                content_type = r.headers.get('Content-Type', 'image/jpeg')
                ext = 'jpg'
                if 'png' in content_type:
                    ext = 'png'
                elif 'jpeg' in content_type or 'jpg' in content_type:
                    ext = 'jpg'
                elif 'webp' in content_type:
                    ext = 'webp'

                # Build unique key under images/ai/<username>/...
                ts = timezone.now().strftime('%Y%m%d_%H%M%S')
                key_relative = f"ai/{username}/{ts}_{uuid.uuid4().hex[:8]}.{ext}"
                s3_key = f"images/{key_relative}"

                # Upload to S3
                s3 = boto3.client('s3',
                                  aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                                  aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                                  region_name=settings.AWS_S3_REGION_NAME)
                s3.put_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                              Key=s3_key,
                              Body=content,
                              ContentType=content_type)

                # Insert into images_table mirroring manual uploads and append AI keywords
                description_keywords = f"{prompt}, ai-generated, AI, AI generated" if prompt else "ai-generated, AI, AI generated"
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO images_table (image_key, description, "user")
                        VALUES (%s, %s, %s)
                        """,
                        [key_relative, description_keywords, username]
                    )

                s3_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com/images/{key_relative}"
                return JsonResponse({'success': True, 'image_url': image_url, 's3_url': s3_url, 'image_key': key_relative})

            except Exception as e:
                # If mirroring fails, still return the generated image URL so the user sees it
                print(f"S3 mirror failed: {e}")
                return JsonResponse({'success': True, 'image_url': image_url, 'mirror_error': str(e)})

        except Exception as e:
            print(f"Error generating image: {str(e)}")  # Add logging
            return JsonResponse({'error': str(e)}, status=500)

    return render(request, 'generate_image.html', {
        'image_url': None,
        'username': username,
        'profile_picture_url': profile_picture_url
    })

# --- Stripe Billing ---
def create_checkout_session(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    load_dotenv()
    stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
    price_id = os.getenv('STRIPE_PRICE_ID')
    app_base_url = os.getenv('APP_BASE_URL', 'http://localhost:8000')

    username = request.session.get('username')
    if not username:
        return JsonResponse({'error': 'login_required'}, status=401)

    try:
        # Try to supply customer email for better linkage (optional)
        customer_email = None
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT email FROM wallify_users WHERE username = %s", [username])
                row = cursor.fetchone()
                if row and row[0]:
                    customer_email = row[0]
        except Exception:
            customer_email = None

        session = stripe.checkout.Session.create(
            mode='subscription',
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            success_url=f"{app_base_url}/?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{app_base_url}/#pricing",
            client_reference_id=username,
            customer_email=customer_email,
            metadata={
                'username': username
            }
        )
        return JsonResponse({'url': session.get('url')})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


from django.views.decorators.csrf import csrf_exempt

@csrf_exempt
def stripe_webhook(request):
    # Verify webhook signature and mark user premium on checkout success
    load_dotenv()
    webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')

    if not webhook_secret:
        return HttpResponse(status=400)

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=webhook_secret
        )
    except Exception:
        return HttpResponse(status=400)

    print('Stripe webhook event:', event.get('type'))
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        md = session.get('metadata') or {}
        username = None
        if isinstance(md, dict):
            username = md.get('username')
        if not username:
            username = session.get('client_reference_id')
        if username:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE wallify_users SET premium = 1 WHERE username = %s", [username])
                # best-effort: if the user is the current session, update session flag
                if request.session.get('username') == username:
                    request.session['is_premium'] = True
            except Exception:
                pass

    return HttpResponse(status=200)

def support(request):
    username = request.session.get('username')
    profile_picture_url = '/static/images/dpfp.png'
    if username:
        profile_picture_url = f'/profile-picture/{username}'

    query = request.GET.get('q', '')
    category = request.GET.get('category', '')
    status = request.GET.get('status', '')
    
    threads = SupportThread.objects.all().order_by('-created_at')
    
    # Apply filters
    if query:
        threads = threads.filter(
            Q(title__icontains=query) |
            Q(messages__content__icontains=query)
        ).distinct()
    if category:
        threads = threads.filter(category=category)
    if status:
        threads = threads.filter(status=status)
    
    # Pagination
    paginator = Paginator(threads, 10)
    page = request.GET.get('page')
    threads = paginator.get_page(page)
    
    return render(request, 'support.html', {
        'threads': threads,
        'query': query,
        'category': category,
        'status': status,
        'username': username,
        'profile_picture_url': profile_picture_url
    })

def thread_detail(request, thread_id):
    username = request.session.get('username')
    profile_picture_url = '/static/images/dpfp.png'
    if username:
        profile_picture_url = f'/profile-picture/{username}'

    thread = get_object_or_404(SupportThread, id=thread_id)
    messages = thread.messages.all().order_by('created_at')
    
    if request.method == 'POST' and not username:
        return JsonResponse({'error': 'login_required'}, status=401)
    
    if request.method == 'POST':
        content = request.POST.get('content')
        if content:
            # Check if user is admin (you'll need to implement this logic)
            is_admin = False  # Add your admin check logic here
            
            ThreadMessage.objects.create(
                thread=thread,
                author_username=username,
                content=content,
                is_admin_reply=is_admin
            )
            return redirect('thread_detail', thread_id=thread_id)
    
    return render(request, 'thread_detail.html', {
        'thread': thread,
        'messages': messages,
        'username': username,
        'profile_picture_url': profile_picture_url
    })

def create_thread(request):
    username = request.session.get('username')
    if not username:
        return redirect('signin')

    if request.method == 'POST':
        title = request.POST.get('title')
        content = request.POST.get('content')
        category = request.POST.get('category')
        
        thread = SupportThread.objects.create(
            title=title,
            author_username=username,
            category=category
        )
        ThreadMessage.objects.create(
            thread=thread,
            author_username=username,
            content=content
        )
        return redirect('thread_detail', thread_id=thread.id)
    
    return render(request, 'create_thread.html', {'username': username})

def change_thread_status(request, thread_id, new_status):
    username = request.session.get('username')
    if not username:
        return redirect('signin')

    thread = get_object_or_404(SupportThread, id=thread_id)
    
    # Check if user is thread owner or admin
    is_admin = False  # Add your admin check logic here
    if username == thread.author_username or is_admin:
        if new_status in ['open', 'resolved', 'closed']:
            thread.status = new_status
            thread.save()
            messages.success(request, f'Thread status updated to {new_status}')
    else:
        messages.error(request, "You don't have permission to change thread status.")
    
    return redirect('thread_detail', thread_id=thread_id)

def delete_thread(request, thread_id):
    username = request.session.get('username')
    if request.method != 'POST':
        return redirect('support')

    if not username:
        messages.error(request, 'You must be logged in to perform this action.')
        return redirect('signin')

    thread = get_object_or_404(SupportThread, id=thread_id)

    # Only the author can delete their thread
    if thread.author_username != username:
        messages.error(request, "You don't have permission to delete this thread.")
        return redirect('thread_detail', thread_id=thread_id)

    # Delete the thread (cascade will remove messages)
    thread.delete()
    messages.success(request, 'Thread deleted successfully.')
    return redirect('support')