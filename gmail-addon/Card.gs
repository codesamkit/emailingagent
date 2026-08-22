/**
 * Contextual side panel for the currently-open Gmail message. Mirrors the
 * detail-pane order in DESIGN.md (score -> summary -> calendar slots ->
 * outline -> draft), rebuilt in CardService's fixed widget set rather than
 * Valence's custom HTML/CSS. Nothing here sends anything — the outline
 * save and draft-expand actions are the same human-in-the-loop endpoints
 * the web UI already calls (api/main.py), never a send/create-event call.
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

  if (
    data.isSchedulingRelated &&
    data.calendarContext &&
    data.calendarContext.suggestedSlots &&
    data.calendarContext.suggestedSlots.length
  ) {
    var calSection = CardService.newCardSection().setHeader('Suggested times');
    data.calendarContext.suggestedSlots.forEach(function (slot) {
      calSection.addWidget(CardService.newTextParagraph().setText(formatSlot_(slot)));
    });
    card.addSection(calSection);
  }

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

function formatSlot_(slot) {
  return new Date(slot.start).toLocaleString() + ' – ' + new Date(slot.end).toLocaleString();
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
