from django.shortcuts import render

def index(request):
    return render(request, 'meta_search/index.html')
