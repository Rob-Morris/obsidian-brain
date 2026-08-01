Feature: Attachment upload boundary

  Scenario: Add a caller-owned file to a required attachment scope
    Given an empty Brain vault for attachment upload
    When I upload attachment "diagram.svg" to "design-assets" with content "<svg>corrected</svg>"
    Then the attachment path is "_Assets/Attachments/design-assets/diagram.svg"
    And the attachment bytes are "<svg>corrected</svg>"
    And the attachment embed is "![[_Assets/Attachments/design-assets/diagram.svg]]"
