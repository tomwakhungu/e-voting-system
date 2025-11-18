from django.shortcuts import render, redirect, reverse
from account.views import account_login
from .models import Position, Candidate, Voter, Votes
from django.http import JsonResponse
from django.utils.text import slugify
from django.contrib import messages
from django.conf import settings
import requests
import json
import random


def index(request):
    if not request.user.is_authenticated:
        return account_login(request)
    return redirect(reverse('voterDashboard'))


def generate_ballot(display_controls=True):
    positions = Position.objects.order_by('priority').all()
    output = ""
    candidates_data = ""
    num = 1

    for position in positions:
        name = position.name
        position_name = slugify(name)
        candidates = Candidate.objects.filter(position=position)

        # Set instruction BEFORE looping candidates (fixes UnboundLocalError)
        if position.max_vote > 1:
            instruction = f"You may select up to {position.max_vote} candidates"
        else:
            instruction = "Select only one candidate"

        # Build candidate list
        for candidate in candidates:
            if position.max_vote > 1:
                input_box = (
                    f'<input type="checkbox" value="{candidate.id}" '
                    f'class="flat-red {position_name}" name="{position_name}[]">'
                )
            else:
                input_box = (
                    f'<input type="radio" value="{candidate.id}" '
                    f'class="flat-red {position_name}" name="{position_name}">'
                )

            image = "/media/" + str(candidate.photo)
            candidates_data += (
                f'<li>{input_box}'
                f'<button type="button" class="btn btn-primary btn-sm btn-flat clist platform" '
                f'data-fullname="{candidate.fullname}" data-bio="{candidate.bio}">'
                f'<i class="fa fa-search"></i> Platform</button>'
                f'<img src="{image}" height="100px" width="100px" class="clist">'
                f'<span class="cname clist">{candidate.fullname}</span></li>'
            )

        # Up/down buttons for admin reordering
        up = 'disabled' if position.priority == 1 else ''
        down = 'disabled' if position.priority == positions.count() else ''

        output += f"""
        <div class="row">
            <div class="col-xs-12">
                <div class="box box-solid" id="{position.id}">
                    <div class="box-header with-border">
                        <h3 class="box-title"><b>{name}</b></h3>"""

        if display_controls:
            output += f"""
                        <div class="pull-right box-tools">
                            <button type="button" class="btn btn-default btn-sm moveup" data-id="{position.id}" {up}>
                                <i class="fa fa-arrow-up"></i>
                            </button>
                            <button type="button" class="btn btn-default btn-sm movedown" data-id="{position.id}" {down}>
                                <i class="fa fa-arrow-down"></i>
                            </button>
                        </div>"""

        output += f"""
                    </div>
                    <div class="box-body">
                        <p>{instruction}
                            <span class="pull-right">
                                <button type="button" class="btn btn-success btn-sm btn-flat reset" data-desc="{position_name}">
                                    <i class="fa fa-refresh"></i> Reset
                                </button>
                            </span>
                        </p>
                        <div id="candidate_list">
                            <ul>{candidates_data}</ul>
                        </div>
                    </div>
                </div>
            </div>
        </div>"""

        position.priority = num
        position.save()
        num += 1
        candidates_data = ''

    return output


def fetch_ballot(request):
    output = generate_ballot(display_controls=True)
    return JsonResponse(output, safe=False)


def generate_otp():
    return "".join([str(random.randint(1, 9)) for _ in range(random.randint(5, 8))])


def dashboard(request):
    user = request.user
    voter = user.voter

    # 1. If voter has already voted → show results
    if voter.voted:
        context = {
            'my_votes': Votes.objects.filter(voter=voter),
            'page_title': 'Thank You for Voting!'
        }
        return render(request, "voting/voter/result.html", context)

    # 2. If voter is not verified → handle OTP
    if voter.otp is None or not voter.verified:
        if not settings.SEND_OTP:
            voter.otp = "0000"
            voter.verified = True
            voter.save()
            messages.success(request, "Verified automatically. You can now vote.")
        else:
            return redirect(reverse('voterVerify'))

    # 3. Otherwise → show ballot
    return redirect(reverse('show_ballot'))


def verify(request):
    return render(request, "voting/voter/verify.html", {'page_title': 'OTP Verification'})


def resend_otp(request):
    voter = request.user.voter
    error = False
    response = ""

    if settings.SEND_OTP:
        if voter.otp_sent >= 3:
            error = True
            response = "You have requested OTP three times. Please use the last one sent."
        else:
            otp = voter.otp or generate_otp()
            voter.otp = otp
            voter.save()

            msg = f"Dear {request.user}, your OTP is {otp}"
            if send_sms(voter.phone, msg):
                voter.otp_sent += 1
                voter.save()
                response = "New OTP sent to your phone."
            else:
                error = True
                response = "Failed to send OTP. Try again."
    else:
        response = "OTP bypassed (SEND_OTP = False)"

    return JsonResponse({"data": response, "error": error})


def bypass_otp():
    Voter.objects.filter(otp=None, verified=False).update(otp="0000", verified=True)
    return "OTP bypassed successfully."


def send_sms(phone_number, msg):
    email = settings.SMS_EMAIL if hasattr(settings, 'SMS_EMAIL') else None
    password = settings.SMS_PASSWORD if hasattr(settings, 'SMS_PASSWORD') else None

    if not email or not password:
        return False

    url = "https://app.multitexter.com/v2/app/sms"
    data = {
        "email": email,
        "password": password,
        "message": msg,
        "sender_name": "OTP",
        "recipients": phone_number,
        "forcednd": 1
    }
    try:
        r = requests.post(url, json=data, headers={'Content-type': 'application/json'})
        return r.json().get('status') == '1'
    except:
        return False


def verify_otp(request):
    if request.method != 'POST':
        messages.error(request, "Access Denied")
        return redirect(reverse('voterVerify'))

    otp = request.POST.get('otp')
    voter = request.user.voter

    if voter.otp == otp or (not settings.SEND_OTP and otp == "0000"):
        voter.verified = True
        voter.save()
        messages.success(request, "Verified! You can now vote.")
        return redirect(reverse('show_ballot'))
    else:
        messages.error(request, "Invalid OTP")
        return redirect(reverse('voterVerify'))


def show_ballot(request):
    if request.user.voter.voted:
        messages.info(request, "You have already voted.")
        return redirect(reverse('voterDashboard'))

    ballot = generate_ballot(display_controls=False)
    context = {'ballot': ballot}
    return render(request, "voting/voter/ballot.html", context)


def preview_vote(request):
    if request.method != 'POST':
        return JsonResponse({"error": True, "list": "Invalid access"})

    form = dict(request.POST)
    form.pop('csrfmiddlewaretoken', None)
    output = ""
    error = False
    response = ""

    for position in Position.objects.all():
        pos = slugify(position.name)
        max_vote = position.max_vote

        if max_vote > 1:
            selected = form.get(pos + "[]", [])
            if len(selected) > max_vote:
                error = True
                response = f"Max {max_vote} candidates allowed for {position.name}"
                break
            if selected:
                output += f"<div class='row votelist'><span class='col-sm-4 pull-right'><b>{position.name}:</b></span><span class='col-sm-8'><ul style='list-style:none;margin-left:-40px'>"
                for cid in selected:
                    try:
                        c = Candidate.objects.get(id=cid, position=position)
                        output += f"<li><i class='fa fa-check-square-o'></i> {c.fullname}</li>"
                    except:
                        pass
                output += "</ul></span></div><hr/>"
        else:
            cid = form.get(pos)
            if cid:
                try:
                    c = Candidate.objects.get(id=cid[0], position=position)
                    output += f"<div class='row votelist'><span class='col-sm-4 pull-right'><b>{position.name}:</b></span><span class='col-sm-8'><i class='fa fa-check-circle-o'></i> {c.fullname}</span></div><hr/>"
                except:
                    pass

    return JsonResponse({"error": error, "list": output or "No votes selected", "response": response})


def submit_ballot(request):
    if request.method != 'POST':
        messages.error(request, "Invalid submission")
        return redirect(reverse('show_ballot'))

    voter = request.user.voter
    if voter.voted:
        messages.error(request, "You have already voted.")
        return redirect(reverse('voterDashboard'))

    form = dict(request.POST)
    form.pop('csrfmiddlewaretoken', None)
    form.pop('submit_vote', None)

    if not form:
        messages.error(request, "Please select at least one candidate.")
        return redirect(reverse('show_ballot'))

    votes_cast = 0
    for position in Position.objects.all():
        pos = slugify(position.name)
        if position.max_vote > 1:
            selected = form.get(pos + "[]", [])
            if len(selected) > position.max_vote:
                messages.error(request, f"Too many selections for {position.name}")
                return redirect(reverse('show_ballot'))
            for cid in selected:
                try:
                    candidate = Candidate.objects.get(id=cid, position=position)
                    Votes.objects.create(voter=voter, candidate=candidate, position=position)
                    votes_cast += 1
                except:
                    pass
        else:
            cid = form.get(pos)
            if cid:
                try:
                    candidate = Candidate.objects.get(id=cid[0], position=position)
                    Votes.objects.create(voter=voter, candidate=candidate, position=position)
                    votes_cast += 1
                except:
                    pass

    if Votes.objects.filter(voter=voter).count() != votes_cast:
        Votes.objects.filter(voter=voter).delete()
        messages.error(request, "Vote failed. Please try again.")
    else:
        voter.voted = True
        voter.save()
        messages.success(request, "Thank you! Your vote has been recorded.")

    return redirect(reverse('voterDashboard'))