cordova.define("com.adjust.sdk.AdjustPlayStorePurchase", function(require, exports, module) {
function AdjustPlayStorePurchase(productId, purchaseToken) {
    this.productId = productId;
    this.purchaseToken = purchaseToken;
}

module.exports = AdjustPlayStorePurchase;

});
