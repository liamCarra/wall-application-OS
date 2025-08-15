from django.db import models
from django.utils import timezone

class WallifyUser(models.Model):
    id = models.IntegerField(
        help_text="Auto-incrementing ID managed by PostgreSQL sequence"
    )  # Maps to auto-incrementing column in PostgreSQL
    username = models.CharField(max_length=50, primary_key=True)  # Primary key
    firstname = models.CharField(max_length=50, null=True)
    lastname = models.CharField(max_length=50, null=True)
    email = models.CharField(max_length=100, null=True)  # Changed from EmailField to match your table
    password = models.CharField(max_length=100)
    pfp = models.BinaryField(null=True)  # For profile picture blob
    
    class Meta:
        db_table = 'wallify_users'  # Specify the existing table name
        managed = False  # Tell Django not to manage this table

    def __str__(self):
        return self.username

class AIGeneration(models.Model):
    id = models.AutoField(primary_key=True)
    username = models.CharField(max_length=50)
    prompt = models.TextField()
    image_url = models.URLField()
    aspect_ratio = models.CharField(max_length=10)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'ai_generations'
        managed = False  # Explicitly set this to True

    def __str__(self):
        return f"{self.username} - {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}"

class SupportThread(models.Model):
    title = models.CharField(max_length=200)
    author_username = models.CharField(max_length=50)  # Changed from ForeignKey
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=[
        ('open', 'Open'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed')
    ], default='open')
    category = models.CharField(max_length=50, choices=[
        ('technical', 'Technical Support'),
        ('order', 'Order Issues'),
        ('general', 'General Questions'),
        ('feedback', 'Feedback')
    ])

    class Meta:
        db_table = 'support_threads'
        managed = False

class ThreadMessage(models.Model):
    thread = models.ForeignKey(SupportThread, on_delete=models.CASCADE, related_name='messages')
    author_username = models.CharField(max_length=50)  # Changed from ForeignKey
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_admin_reply = models.BooleanField(default=False)

    class Meta:
        db_table = 'thread_messages'
        managed = False