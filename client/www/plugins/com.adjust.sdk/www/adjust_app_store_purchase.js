cordova.define("com.adjust.sdk.AdjustAppStorePurchase", function(require, exports, module) {
function AdjustAppStorePurchase(receipt, productId, transactionId) {
    this.receipt = receipt;
    this.productId = productId;
    this.transactionId = transactionId;
}

module.exports = AdjustAppStorePurchase;

});
