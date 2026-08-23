/**
 * Contextual side panel for the currently-open Gmail message. Mirrors the
 * detail-pane order in DESIGN.md (score -> summary -> calendar slots ->
 * outline -> draft), rebuilt in CardService's fixed widget set rather than
 * Valence's custom HTML/CSS. Nothing here sends email, and every calendar
 * write (create/reschedule/rename/cancel) only happens from an explicit
 * button tap in this panel, hitting the same human-in-the-loop endpoints
 * the web UI would (api/main.py) — never automatically.
 */

function onHomepage(e) {
  return CardService.newCardBuilder()
    .setHeader(CardService.newCardHeader().setTitle('Valence'))
    .addSection(
      CardService.newCardSection().addWidget(
        CardService.newTextParagraph().setText('Open an email to see its Valence review.')
      )
    )
    .build();
}

function onGmailMessage(e) {
  var messageId = e.gmail.messageId;
  var data;
  try {
    data = apiGetEmail_(messageId);
  } catch (err) {
    return buildErrorCard_(err);
  }
  return data ? buildEmailCard_(data) : buildNotProcessedCard_();
}

function buildEmailCard_(data) {
  var card = CardService.newCardBuilder().setHeader(
    CardService.newCardHeader()
      .setTitle(data.subject || '(no subject)')
      .setSubtitle(data.sender)
  );

  var overview = CardService.newCardSection();
  overview.addWidget(
    CardService.newKeyValue()
      .setTopLabel('Importance')
      .setContent(
        (data.importanceLevel || 'unscored') +
          (data.importanceScore != null ? ' · ' + Math.round(data.importanceScore) : '')
      )
  );
  if (data.isNoReply) {
    overview.addWidget(CardService.newTextParagraph().setText('<b>No-Reply</b> — informational only'));
  }
  if (data.summary) {
    overview.addWidget(CardService.newTextParagraph().setText(data.summary));
  }
  if (data.mentionedDates && data.mentionedDates.length) {
    overview.addWidget(
      CardService.newKeyValue().setTopLabel('Dates mentioned').setContent(data.mentionedDates.join(', '))
    );
  }
  card.addSection(overview);

  var calSection = buildCalendarSection_(data);
  if (calSection) card.addSection(calSection);

  var outlineSection = CardService.newCardSection().setHeader('Reply outline');
  if (data.outlineEligible) {
    outlineSection.addWidget(
      CardService.newTextInput()
        .setFieldName('outlineText')
        .setTitle('One bullet per line')
        .setMultiline(true)
        .setValue((data.replyOutline || []).join('\n'))
    );
    outlineSection.addWidget(
      CardService.newButtonSet()
        .addButton(
          CardService.newTextButton()
            .setText('Save outline')
            .setOnClickAction(
              CardService.newAction().setFunctionName('onSaveOutline').setParameters({ emailId: data.emailId })
            )
        )
        .addButton(
          CardService.newTextButton()
            .setText('Expand to draft')
            .setOnClickAction(
              CardService.newAction().setFunctionName('onExpandDraft').setParameters({ emailId: data.emailId })
            )
        )
    );
  } else {
    outlineSection.addWidget(CardService.newTextParagraph().setText(outlinePlaceholder_(data.replyOutlineStatus)));
  }
  card.addSection(outlineSection);

  return card.build();
}

/**
 * The calendar section reflects `proposedEventStatus`:
 *   suggested/failed -> the pipeline extracted a candidate meeting; offer
 *                        Approve (creates it for real) / Decline (discard).
 *   approved         -> a real event exists; offer Reschedule/rename/Cancel.
 *   none/declined    -> nothing to show.
 */
function buildCalendarSection_(data) {
  var event = data.proposedEvent;
  var status = data.proposedEventStatus;
  if (!event || status === 'none' || status === 'declined') return null;

  var section = CardService.newCardSection().setHeader('Proposed calendar event');
  section.addWidget(
    CardService.newKeyValue().setTopLabel(event.title || '(untitled)').setContent(formatEvent_(event))
  );
  if (event.location) {
    section.addWidget(CardService.newTextParagraph().setText('📍 ' + event.location));
  }

  if (status === 'approved') {
    section.addWidget(CardService.newTextParagraph().setText('✓ On your Google Calendar'));
    var approvedButtons = CardService.newButtonSet();
    approvedButtons.addButton(
      CardService.newTextButton()
        .setText('Reschedule / rename')
        .setOnClickAction(
          CardService.newAction().setFunctionName('onOpenEventForm').setParameters({ emailId: data.emailId })
        )
    );
    approvedButtons.addButton(
      CardService.newTextButton()
        .setText('Cancel event')
        .setOnClickAction(
          CardService.newAction().setFunctionName('onOpenCancelConfirm').setParameters({ emailId: data.emailId })
        )
    );
    section.addWidget(approvedButtons);
    return section;
  }

  // suggested or failed
  if (status === 'failed') {
    section.addWidget(
      CardService.newTextParagraph().setText('Creating it failed: ' + (event.error || 'unknown error'))
    );
  }
  var decideButtons = CardService.newButtonSet();
  decideButtons.addButton(
    CardService.newTextButton()
      .setText(status === 'failed' ? 'Retry' : 'Add to calendar')
      .setOnClickAction(
        CardService.newAction().setFunctionName('onApproveEvent').setParameters({ emailId: data.emailId })
      )
  );
  decideButtons.addButton(
    CardService.newTextButton()
      .setText('Dismiss')
      .setOnClickAction(
        CardService.newAction().setFunctionName('onDeclineEvent').setParameters({ emailId: data.emailId })
      )
  );
  section.addWidget(decideButtons);
  return section;
}

function buildEventFormCard_(data) {
  var event = data.proposedEvent || {};
  var defaultStart = event.start ? new Date(event.start) : new Date(Date.now() + 60 * 60 * 1000);
  var defaultEnd = event.end ? new Date(event.end) : new Date(defaultStart.getTime() + 30 * 60 * 1000);

  var card = CardService.newCardBuilder().setHeader(
    CardService.newCardHeader().setTitle('Reschedule / rename event')
  );
  var section = CardService.newCardSection();
  section.addWidget(
    CardService.newTextInput().setFieldName('summary').setTitle('Title').setValue(event.title || '')
  );
  section.addWidget(
    CardService.newDateTimePicker()
      .setFieldName('start')
      .setTitle('Start')
      .setValueInMsSinceEpoch(defaultStart.getTime())
  );
  section.addWidget(
    CardService.newDateTimePicker()
      .setFieldName('end')
      .setTitle('End')
      .setValueInMsSinceEpoch(defaultEnd.getTime())
  );
  section.addWidget(
    CardService.newTextInput().setFieldName('location').setTitle('Location (optional)').setValue(event.location || '')
  );
  section.addWidget(
    CardService.newTextButton()
      .setText('Save changes')
      .setOnClickAction(
        CardService.newAction().setFunctionName('onSaveEvent').setParameters({ emailId: data.emailId })
      )
  );
  card.addSection(section);
  return card.build();
}

function onOpenEventForm(e) {
  var emailId = e.parameters.emailId;
  var data;
  try {
    data = apiGetEmail_(emailId);
  } catch (err) {
    return notify_('Could not load email: ' + err.message);
  }
  if (!data) return notify_('This email is no longer available.');
  return CardService.newActionResponseBuilder()
    .setNavigation(CardService.newNavigation().pushCard(buildEventFormCard_(data)))
    .build();
}

function onSaveEvent(e) {
  var emailId = e.parameters.emailId;
  var form = e.formInput || {};

  var payload = {};
  if (form.summary) payload.summary = form.summary;
  if (form.location) payload.location = form.location;
  if (form.start) payload.start = new Date(parseInt(form.start, 10)).toISOString();
  if (form.end) payload.end = new Date(parseInt(form.end, 10)).toISOString();

  var data;
  try {
    data = apiUpdateEvent_(emailId, payload);
  } catch (err) {
    return notify_('Update failed: ' + err.message);
  }
  return CardService.newActionResponseBuilder()
    .setNavigation(CardService.newNavigation().popToRoot().updateCard(buildEmailCard_(data)))
    .setNotification(CardService.newNotification().setText('Event updated.'))
    .build();
}

function onApproveEvent(e) {
  var emailId = e.parameters.emailId;
  var data;
  try {
    data = apiApproveEvent_(emailId);
  } catch (err) {
    return notify_('Could not create the event: ' + err.message);
  }
  return CardService.newActionResponseBuilder()
    .setNavigation(CardService.newNavigation().updateCard(buildEmailCard_(data)))
    .setNotification(CardService.newNotification().setText('Event created.'))
    .build();
}

function onDeclineEvent(e) {
  var emailId = e.parameters.emailId;
  var data;
  try {
    data = apiDeclineEvent_(emailId);
  } catch (err) {
    return notify_('Decline failed: ' + err.message);
  }
  return CardService.newActionResponseBuilder()
    .setNavigation(CardService.newNavigation().updateCard(buildEmailCard_(data)))
    .build();
}

function onOpenCancelConfirm(e) {
  var emailId = e.parameters.emailId;
  var confirmCard = CardService.newCardBuilder()
    .setHeader(CardService.newCardHeader().setTitle('Cancel this event?'))
    .addSection(
      CardService.newCardSection()
        .addWidget(
          CardService.newTextParagraph().setText(
            'This deletes the event from Google Calendar. This cannot be undone from here.'
          )
        )
        .addWidget(
          CardService.newTextButton()
            .setText('Yes, cancel event')
            .setOnClickAction(
              CardService.newAction().setFunctionName('onCancelEvent').setParameters({ emailId: emailId })
            )
        )
    )
    .build();
  return CardService.newActionResponseBuilder()
    .setNavigation(CardService.newNavigation().pushCard(confirmCard))
    .build();
}

function onCancelEvent(e) {
  var emailId = e.parameters.emailId;
  var data;
  try {
    data = apiCancelEvent_(emailId);
  } catch (err) {
    return notify_('Cancel failed: ' + err.message);
  }
  return CardService.newActionResponseBuilder()
    .setNavigation(CardService.newNavigation().popToRoot().updateCard(buildEmailCard_(data)))
    .setNotification(CardService.newNotification().setText('Event cancelled.'))
    .build();
}

function onSaveOutline(e) {
  var emailId = e.parameters.emailId;
  var lines = (e.formInput.outlineText || '')
    .split('\n')
    .map(function (l) {
      return l.trim();
    })
    .filter(Boolean);

  var data;
  try {
    data = apiSaveOutline_(emailId, lines);
  } catch (err) {
    return notify_('Save failed: ' + err.message);
  }
  return CardService.newActionResponseBuilder()
    .setNavigation(CardService.newNavigation().updateCard(buildEmailCard_(data)))
    .setNotification(CardService.newNotification().setText('Outline saved.'))
    .build();
}

function onExpandDraft(e) {
  var emailId = e.parameters.emailId;
  var result;
  try {
    result = apiExpandDraft_(emailId);
  } catch (err) {
    return notify_('Expand failed: ' + err.message);
  }
  if (result.notImplemented) {
    return notify_("Expand-to-draft isn't implemented yet.");
  }

  var draftCard = CardService.newCardBuilder()
    .setHeader(CardService.newCardHeader().setTitle('Draft'))
    .addSection(CardService.newCardSection().addWidget(CardService.newTextParagraph().setText(esc_(result.draft))))
    .build();
  return CardService.newActionResponseBuilder()
    .setNavigation(CardService.newNavigation().pushCard(draftCard))
    .build();
}

function outlinePlaceholder_(status) {
  if (status === 'not_applicable') return 'No-Reply — no response needed.';
  return 'Unread — no outline yet.';
}

function formatEvent_(event) {
  return new Date(event.start).toLocaleString() + ' – ' + new Date(event.end).toLocaleString();
}

function esc_(text) {
  return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/\n/g, '<br>');
}

function buildNotProcessedCard_() {
  return CardService.newCardBuilder()
    .setHeader(CardService.newCardHeader().setTitle('Not processed yet'))
    .addSection(
      CardService.newCardSection().addWidget(
        CardService.newTextParagraph().setText(
          "This email hasn't been through the Valence pipeline yet — it should show up after the next scheduled run."
        )
      )
    )
    .build();
}

function buildErrorCard_(err) {
  return CardService.newCardBuilder()
    .setHeader(CardService.newCardHeader().setTitle('Valence error'))
    .addSection(CardService.newCardSection().addWidget(CardService.newTextParagraph().setText(esc_(err.message))))
    .build();
}

function notify_(text) {
  return CardService.newActionResponseBuilder().setNotification(CardService.newNotification().setText(text)).build();
}
